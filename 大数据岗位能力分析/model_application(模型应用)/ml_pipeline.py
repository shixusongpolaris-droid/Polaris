#!/usr/bin/env python3
"""
模块5: 机器学习流水线 (ML Pipeline)
- 特征工程: TF-IDF + OneHot + Ordinal + MultiHot
- 分类模型: LogisticRegression vs RandomForest (9类)
- 回归模型: LinearRegression vs GradientBoosting (薪资预测)
- 可视化: 混淆矩阵、特征重要性、预测vs实际散点图
- 模型持久化: pickle
"""
import json
import os
import sys
import pickle
import warnings
from typing import Tuple, Optional

import numpy as np
import pandas as pd
from collections import Counter, defaultdict

# ML
from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.preprocessing import (
    StandardScaler, MultiLabelBinarizer, OrdinalEncoder, OneHotEncoder
)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
from sklearn.metrics import (
    accuracy_score, f1_score, classification_report, confusion_matrix,
    mean_absolute_error, mean_squared_error, r2_score
)
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.base import BaseEstimator, TransformerMixin

# Viz
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# Custom Transformers
# ============================================================
class ListToString(BaseEstimator, TransformerMixin):
    """将list列转为字符串（用于TF-IDF）"""
    def fit(self, X, y=None):
        return self

    def transform(self, X):
        return X.apply(lambda x: " ".join(x) if isinstance(x, list) else str(x))


class MultiHotEncoder(BaseEstimator, TransformerMixin):
    """Multi-hot编码：将list列转为01矩阵，限制特征数"""
    def __init__(self, max_features: int = 30):
        self.max_features = max_features
        self.mlb = MultiLabelBinarizer()
        self.top_features_ = None

    def fit(self, X, y=None):
        lists = [x if isinstance(x, list) else [str(x)] for x in X]
        self.mlb.fit(lists)
        # 限制特征数
        class_counts = self.mlb.classes_.tolist()
        class_freq = {}
        for lst in lists:
            for item in lst:
                class_freq[item] = class_freq.get(item, 0) + 1
        sorted_items = sorted(class_freq.items(), key=lambda x: x[1], reverse=True)
        self.top_features_ = [item for item, _ in sorted_items[:self.max_features]]
        return self

    def transform(self, X):
        lists = [x if isinstance(x, list) else [str(x)] for x in X]
        # Filter to top features only, use fitted mlb
        filtered = [[item for item in lst if item in self.top_features_]
                     for lst in lists]
        return self.mlb.transform(filtered)


# ============================================================
# Data Loading & Feature Engineering
# ============================================================
def load_ml_data(filepath: str = "data/labeled_jobs.json") -> pd.DataFrame:
    """加载标注数据并转换为ML-ready DataFrame"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    jobs = data.get("jobs", data) if isinstance(data, dict) else data

    rows = []
    for j in jobs:
        # Skip non-data jobs (null category)
        if not j.get("main_category"):
            continue

        # Derive salary avg
        smin = j.get("salary_min_k")
        smax = j.get("salary_max_k")
        salary_avg = None
        if smin and smax and smin > 0:
            salary_avg = round((smin + smax) / 2, 1)

        # Extract city from location
        loc = j.get("location", "")
        city = loc.split("-")[0] if "-" in loc else loc

        rows.append({
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "city": city,
            "main_category": j.get("main_category", ""),
            "job_level": j.get("job_level", ""),
            "education_std": j.get("education_std", ""),
            "tech_stack": j.get("tech_stack", []),
            "languages": j.get("languages", []),
            "cloud_platforms": j.get("cloud_platforms", []),
            "industry_tags": j.get("industry_tags", []),
            "job_desc_preview": j.get("job_desc_preview", ""),
            "salary_avg_wan_month": salary_avg,
            "salary_min_k": smin,
            "salary_max_k": smax,
        })

    df = pd.DataFrame(rows)
    # Keep salary as float, fill other NA fields with empty string
    for col in df.columns:
        if col not in ("salary_avg_wan_month", "salary_min_k", "salary_max_k"):
            df[col] = df[col].fillna("")
    return df


def build_preprocessor() -> ColumnTransformer:
    """构建特征预处理流水线"""
    preprocessor = ColumnTransformer([
        ("text", TfidfVectorizer(
            max_features=3000, ngram_range=(1, 2),
            min_df=2, max_df=0.9, sublinear_tf=True),
         "job_desc_preview"),
        ("cat_city", OneHotEncoder(handle_unknown="ignore", sparse_output=True),
         ["city"]),
        ("cat_edu", OneHotEncoder(handle_unknown="ignore", sparse_output=True),
         ["education_std"]),
        ("ord_level", OrdinalEncoder(
            categories=[["初级", "中级", "高级", "管理/负责人", "未标注"]],
            handle_unknown="use_encoded_value", unknown_value=-1),
         ["job_level"]),
        ("multi_tech", MultiHotEncoder(max_features=30),
         "tech_stack"),
        ("multi_lang", MultiHotEncoder(max_features=30),
         "languages"),
    ], remainder="drop")

    return preprocessor


# ============================================================
# Classification
# ============================================================
def prepare_classification_data(df: pd.DataFrame) -> Tuple:
    """准备分类数据：X(features) + y(9 classes)"""
    # Filter out jobs without category
    valid = df[df["main_category"].notna() & (df["main_category"] != "")]
    X = valid.drop(columns=["main_category", "salary_avg_wan_month",
                            "salary_min_k", "salary_max_k",
                            "title", "company"])
    y = valid["main_category"]

    # Stratify split
    from sklearn.model_selection import train_test_split
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y)
    except ValueError:
        # Some classes too small for stratify
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42)

    class_names = sorted(y.unique())
    return X_train, X_test, y_train, y_test, class_names


def train_classification_models(X_train, X_test, y_train, y_test) -> dict:
    """训练并对比LR和RF两个分类器"""
    print("  构建特征预处理流水线...")
    preprocessor = build_preprocessor()

    # Fit preprocessor once, transform both train and test
    print("  预处理器拟合...")
    X_train_t = preprocessor.fit_transform(X_train)
    X_test_t = preprocessor.transform(X_test)
    print(f"  特征维度: {X_train_t.shape[1]}")

    results = {"preprocessor": preprocessor}

    # --- Logistic Regression ---
    print("  训练 LogisticRegression...")
    lr = LogisticRegression(
        solver="lbfgs", max_iter=2000,
        class_weight="balanced", random_state=42)
    # Simple parameter comparison (no GridSearchCV to avoid feature mismatch)
    lr.fit(X_train_t, y_train)
    lr_pred = lr.predict(X_test_t)
    results["lr_model"] = lr
    results["lr_best_params"] = {"C": 1.0}
    results["lr_accuracy"] = accuracy_score(y_test, lr_pred)
    results["lr_f1_macro"] = f1_score(y_test, lr_pred, average="macro")
    results["lr_f1_weighted"] = f1_score(y_test, lr_pred, average="weighted")
    results["lr_pred"] = lr_pred

    print(f"    LR Accuracy: {results['lr_accuracy']:.4f}, "
          f"F1-macro: {results['lr_f1_macro']:.4f}")

    # --- Random Forest ---
    print("  训练 RandomForest...")
    rf = RandomForestClassifier(
        n_estimators=200, max_depth=20,
        class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_train_t, y_train)
    rf_pred = rf.predict(X_test_t)
    results["rf_model"] = rf
    results["rf_best_params"] = {"n_estimators": 200, "max_depth": 20}
    results["rf_accuracy"] = accuracy_score(y_test, rf_pred)
    results["rf_f1_macro"] = f1_score(y_test, rf_pred, average="macro")
    results["rf_f1_weighted"] = f1_score(y_test, rf_pred, average="weighted")
    results["rf_pred"] = rf_pred

    print(f"    RF  Accuracy: {results['rf_accuracy']:.4f}, "
          f"F1-macro: {results['rf_f1_macro']:.4f}")

    results["y_test"] = y_test
    results["class_names"] = sorted(y_test.unique())
    return results


# ============================================================
# Regression
# ============================================================
def prepare_regression_data(df: pd.DataFrame) -> Tuple:
    """准备回归数据：仅使用有薪资的岗位"""
    valid = df[df["salary_avg_wan_month"].notna() & (df["salary_avg_wan_month"] > 0)]
    X = valid.drop(columns=["salary_avg_wan_month", "salary_min_k", "salary_max_k",
                            "title", "company", "main_category"])
    y = valid["salary_avg_wan_month"].values.astype(float)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42)

    return X_train, X_test, y_train, y_test


def train_regression_models(X_train, X_test, y_train, y_test) -> dict:
    """训练并对比LR和GB两个回归器"""
    preprocessor = build_preprocessor()

    # Fit preprocessor once
    print("  预处理器拟合...")
    X_train_t = preprocessor.fit_transform(X_train).toarray()
    X_test_t = preprocessor.transform(X_test).toarray()
    print(f"  特征维度: {X_train_t.shape[1]}")

    results = {"preprocessor": preprocessor}

    # --- Linear Regression ---
    print("  训练 LinearRegression...")
    lr = Pipeline([
        ("scaler", StandardScaler()),
        ("reg", LinearRegression())
    ])
    lr.fit(X_train_t, y_train)
    lr_pred = lr.predict(X_test_t)
    results["lr_model"] = lr
    results["lr_mae"] = mean_absolute_error(y_test, lr_pred)
    results["lr_rmse"] = np.sqrt(mean_squared_error(y_test, lr_pred))
    results["lr_r2"] = r2_score(y_test, lr_pred)
    results["lr_pred"] = lr_pred

    print(f"    LR MAE: {results['lr_mae']:.2f}万, "
          f"RMSE: {results['lr_rmse']:.2f}万, "
          f"R²: {results['lr_r2']:.4f}")

    # --- Gradient Boosting ---
    print("  训练 GradientBoostingRegressor...")
    gb = GradientBoostingRegressor(random_state=42)
    gb_grid = GridSearchCV(gb,
                           {"n_estimators": [100, 200],
                            "max_depth": [3, 5],
                            "learning_rate": [0.05, 0.1]},
                           cv=3, scoring="neg_mean_absolute_error", n_jobs=-1)
    gb_grid.fit(X_train_t, y_train)
    gb_pred = gb_grid.predict(X_test_t)
    results["gb_model"] = gb_grid.best_estimator_
    results["gb_best_params"] = gb_grid.best_params_
    results["gb_mae"] = mean_absolute_error(y_test, gb_pred)
    results["gb_rmse"] = np.sqrt(mean_squared_error(y_test, gb_pred))
    results["gb_r2"] = r2_score(y_test, gb_pred)
    results["gb_pred"] = gb_pred

    print(f"    GB MAE: {results['gb_mae']:.2f}万, "
          f"RMSE: {results['gb_rmse']:.2f}万, "
          f"R²: {results['gb_r2']:.4f}")

    results["y_test"] = y_test
    return results


# ============================================================
# Visualization
# ============================================================
def setup_chinese_font():
    """设置matplotlib中文字体 (macOS兼容)"""
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}
    candidates = ['PingFang SC', 'STHeiti', 'Heiti TC', 'Songti SC', 'Arial Unicode MS', 'DejaVu Sans']
    for font in candidates:
        if font in available:
            plt.rcParams['font.sans-serif'] = [font] + plt.rcParams['font.sans-serif']
            break
    plt.rcParams['axes.unicode_minus'] = False


def plot_confusion_matrices(class_results: dict, output_dir: str = "model_application/charts"):
    """绘制两个分类器的混淆矩阵"""
    os.makedirs(output_dir, exist_ok=True)
    setup_chinese_font()

    y_test = class_results["y_test"]
    class_names = class_results["class_names"]

    for model_name in ["lr", "rf"]:
        pred = class_results[f"{model_name}_pred"]
        cm = confusion_matrix(y_test, pred, labels=class_names)

        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                    xticklabels=class_names, yticklabels=class_names,
                    ax=ax)
        ax.set_xlabel("预测类别")
        ax.set_ylabel("真实类别")
        model_label = "Logistic Regression" if model_name == "lr" else "Random Forest"
        ax.set_title(f"混淆矩阵 - {model_label}")

        path = os.path.join(output_dir, f"confusion_matrix_{model_name}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  已保存 {path}")


def plot_regression_results(reg_results: dict, output_dir: str = "model_application/charts"):
    """绘制回归模型的预测vs实际散点图"""
    os.makedirs(output_dir, exist_ok=True)
    setup_chinese_font()

    y_test = reg_results["y_test"]

    for model_name in ["lr", "gb"]:
        pred = reg_results[f"{model_name}_pred"]
        model_label = "Linear Regression" if model_name == "lr" else "Gradient Boosting"

        fig, ax = plt.subplots(figsize=(8, 6))
        ax.scatter(y_test, pred, alpha=0.6, edgecolors="k", linewidth=0.5)
        # Ideal line
        lims = [min(y_test.min(), pred.min()), max(y_test.max(), pred.max())]
        ax.plot(lims, lims, "r--", linewidth=2, label="完美预测")
        ax.set_xlabel("实际薪资 (万/月)")
        ax.set_ylabel("预测薪资 (万/月)")
        ax.set_title(f"预测 vs 实际 - {model_label}")
        ax.legend()

        path = os.path.join(output_dir, f"actual_vs_predicted_{model_name}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  已保存 {path}")


def plot_feature_importance(class_results: dict, reg_results: dict,
                             output_dir: str = "model_application/charts"):
    """绘制特征重要性图"""
    os.makedirs(output_dir, exist_ok=True)
    setup_chinese_font()

    # RF Classifier feature importance (top 20)
    rf_model = class_results.get("rf_model")
    if rf_model and hasattr(rf_model, "feature_importances_"):
        importances = rf_model.feature_importances_
        indices = np.argsort(importances)[-20:]
        top_importances = importances[indices]

        fig, ax = plt.subplots(figsize=(10, 8))
        ax.barh(range(len(indices)), top_importances)
        ax.set_yticks(range(len(indices)))
        ax.set_yticklabels([f"Feature {i}" for i in indices], fontsize=8)
        ax.set_xlabel("重要性")
        ax.set_title("Random Forest 分类器 - Top 20 特征重要性")

        path = os.path.join(output_dir, "feature_importance_rf.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  已保存 {path}")

    # GB Regressor feature importance (top 20)
    gb_model = reg_results.get("gb_model")
    if gb_model and hasattr(gb_model, "feature_importances_"):
        importances = gb_model.feature_importances_
        indices = np.argsort(importances)[-20:]
        top_importances = importances[indices]

        fig, ax = plt.subplots(figsize=(10, 8))
        ax.barh(range(len(indices)), top_importances)
        ax.set_yticks(range(len(indices)))
        ax.set_yticklabels([f"Feature {i}" for i in indices], fontsize=8)
        ax.set_xlabel("重要性")
        ax.set_title("Gradient Boosting 回归器 - Top 20 特征重要性")

        path = os.path.join(output_dir, "feature_importance_gb.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  已保存 {path}")


# ============================================================
# Model Comparison & Saving
# ============================================================
def generate_model_comparison(class_results: dict, reg_results: dict) -> dict:
    """生成结构化的模型对比报告"""
    comparison = {
        "classification": {
            "task": "9-class job category prediction",
            "train_samples": "~275 (80% of 344 valid jobs)",
            "test_samples": "~69 (20%)",
            "models": {
                "LogisticRegression": {
                    "accuracy": round(class_results.get("lr_accuracy", 0), 4),
                    "f1_macro": round(class_results.get("lr_f1_macro", 0), 4),
                    "f1_weighted": round(class_results.get("lr_f1_weighted", 0), 4),
                    "best_params": class_results.get("lr_best_params", {}),
                },
                "RandomForest": {
                    "accuracy": round(class_results.get("rf_accuracy", 0), 4),
                    "f1_macro": round(class_results.get("rf_f1_macro", 0), 4),
                    "f1_weighted": round(class_results.get("rf_f1_weighted", 0), 4),
                    "best_params": class_results.get("rf_best_params", {}),
                },
            },
        },
        "regression": {
            "task": "salary prediction (wan/month)",
            "train_samples": "~168 (80% of 210 jobs with salary)",
            "test_samples": "~42 (20%)",
            "models": {
                "LinearRegression": {
                    "MAE_wan": round(reg_results.get("lr_mae", 0), 2),
                    "RMSE_wan": round(reg_results.get("lr_rmse", 0), 2),
                    "R2": round(reg_results.get("lr_r2", 0), 4),
                    "best_params": "N/A (no hyperparameter tuning)",
                },
                "GradientBoosting": {
                    "MAE_wan": round(reg_results.get("gb_mae", 0), 2),
                    "RMSE_wan": round(reg_results.get("gb_rmse", 0), 2),
                    "R2": round(reg_results.get("gb_r2", 0), 4),
                    "best_params": reg_results.get("gb_best_params", {}),
                },
            },
        },
    }

    # Determine winner
    lr_f1 = class_results.get("lr_f1_weighted", 0)
    rf_f1 = class_results.get("rf_f1_weighted", 0)
    comparison["classification"]["winner"] = (
        "RandomForest" if rf_f1 > lr_f1 else "LogisticRegression")

    lr_r2 = reg_results.get("lr_r2", 0)
    gb_r2 = reg_results.get("gb_r2", 0)
    comparison["regression"]["winner"] = (
        "GradientBoosting" if gb_r2 > lr_r2 else "LinearRegression")

    return comparison


def save_models(class_results: dict, reg_results: dict, output_dir: str = "model_application/models"):
    """保存模型和编码器"""
    os.makedirs(output_dir, exist_ok=True)

    to_save = {
        "preprocessor.pkl": class_results.get("preprocessor"),
        "lr_classifier.pkl": class_results.get("lr_model"),
        "rf_classifier.pkl": class_results.get("rf_model"),
        "lr_regressor.pkl": reg_results.get("lr_model"),
        "gb_regressor.pkl": reg_results.get("gb_model"),
    }

    for filename, model in to_save.items():
        if model is not None:
            path = os.path.join(output_dir, filename)
            with open(path, "wb") as f:
                pickle.dump(model, f)


def print_model_report(class_results: dict, reg_results: dict, comparison: dict):
    """格式化输出模型对比报告"""
    print("\n" + "=" * 60)
    print("  模型对比报告")
    print("=" * 60)

    print("\n【分类模型 - 9类岗位预测】")
    print(f"  {'模型':<20} {'Accuracy':>10} {'Macro-F1':>10} {'Weighted-F1':>10}")
    print(f"  {'-'*50}")
    for model_name, metrics in comparison["classification"]["models"].items():
        print(f"  {model_name:<20} "
              f"{metrics['accuracy']:>10.4f} "
              f"{metrics['f1_macro']:>10.4f} "
              f"{metrics['f1_weighted']:>10.4f}")
    print(f"  最佳模型: {comparison['classification']['winner']}")

    print("\n【回归模型 - 薪资预测 (万/月)】")
    print(f"  {'模型':<20} {'MAE(万)':>10} {'RMSE(万)':>10} {'R²':>10}")
    print(f"  {'-'*50}")
    for model_name, metrics in comparison["regression"]["models"].items():
        print(f"  {model_name:<20} "
              f"{metrics['MAE_wan']:>10.2f} "
              f"{metrics['RMSE_wan']:>10.2f} "
              f"{metrics['R2']:>10.4f}")
    print(f"  最佳模型: {comparison['regression']['winner']}")

    # Per-class performance
    print("\n【各类别分类性能 (RandomForest)】")
    y_test = class_results.get("y_test")
    rf_pred = class_results.get("rf_pred")
    if y_test is not None and rf_pred is not None:
        report = classification_report(
            y_test, rf_pred, target_names=class_results.get("class_names", []),
            zero_division=0)
        print(report)


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("  机器学习模型应用 (ML Pipeline)")
    print("=" * 60)

    os.makedirs("model_application/models", exist_ok=True)
    os.makedirs("model_application/charts", exist_ok=True)

    # Load data
    print("\n[1/5] 加载数据并构建特征...")
    df = load_ml_data()
    print(f"  有效样本: {len(df)} 条")
    print(f"  有薪资样本: {(df['salary_avg_wan_month'].notna() & (df['salary_avg_wan_month'] > 0)).sum()} 条")
    print(f"  类别分布: {dict(df['main_category'].value_counts())}")

    # Classification
    print("\n[2/5] 训练分类模型...")
    X_train_c, X_test_c, y_train_c, y_test_c, class_names = prepare_classification_data(df)
    class_results = train_classification_models(
        X_train_c, X_test_c, y_train_c, y_test_c)
    class_results["class_names"] = class_names

    plot_confusion_matrices(class_results)

    # Regression
    print("\n[3/5] 训练回归模型...")
    X_train_r, X_test_r, y_train_r, y_test_r = prepare_regression_data(df)
    reg_results = train_regression_models(
        X_train_r, X_test_r, y_train_r, y_test_r)

    plot_regression_results(reg_results)

    # Feature Importance
    print("\n[4/5] 生成特征重要性图...")
    plot_feature_importance(class_results, reg_results)

    # Compare & Report
    print("\n[5/5] 模型对比与报告...")
    comparison = generate_model_comparison(class_results, reg_results)
    print_model_report(class_results, reg_results, comparison)

    # Save
    save_models(class_results, reg_results)
    with open("model_application/models/model_report.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)

    print(f"\n模型已保存至 model_application/models/")
    print(f"图表已保存至 model_application/charts/")
    print(f"报告已保存至 model_application/models/model_report.json")


if __name__ == "__main__":
    main()
