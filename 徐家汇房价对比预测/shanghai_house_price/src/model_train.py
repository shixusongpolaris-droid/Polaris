"""
线性回归模型训练与评估模块
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import train_test_split, cross_val_score, KFold
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.metrics import (
    r2_score, mean_squared_error, mean_absolute_error,
    explained_variance_score
)
from sklearn.preprocessing import StandardScaler

# 设置中文字体
plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "Heiti TC", "Songti SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def train_linear_regression(X, y, feature_names, test_size=0.2, random_state=42):
    """
    训练线性回归模型并评估

    参数:
        X: 特征矩阵
        y: 目标变量
        feature_names: 特征名称列表
        test_size: 测试集比例
        random_state: 随机种子
    """
    print("\n" + "=" * 60)
    print("             上海徐家汇区域二手房价格线性回归模型")
    print("=" * 60)

    # --- 1. 数据标准化 ---
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # --- 2. 划分训练集和测试集 ---
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=test_size, random_state=random_state
    )
    print(f"\n训练集样本数: {X_train.shape[0]}")
    print(f"测试集样本数: {X_test.shape[0]}")

    # --- 3. 训练线性回归模型 ---
    lr = LinearRegression()
    lr.fit(X_train, y_train)

    # --- 4. 预测 ---
    y_train_pred = lr.predict(X_train)
    y_test_pred = lr.predict(X_test)

    # --- 5. 模型评估 ---
    print("\n" + "-" * 40)
    print("           模型评估指标")
    print("-" * 40)

    train_r2 = r2_score(y_train, y_train_pred)
    test_r2 = r2_score(y_test, y_test_pred)
    test_mse = mean_squared_error(y_test, y_test_pred)
    test_rmse = np.sqrt(test_mse)
    test_mae = mean_absolute_error(y_test, y_test_pred)
    test_evs = explained_variance_score(y_test, y_test_pred)

    print(f"训练集 R²:          {train_r2:.4f}")
    print(f"测试集 R²:          {test_r2:.4f}")
    print(f"测试集 MSE:         {test_mse:.2f}")
    print(f"测试集 RMSE:        {test_rmse:.2f} 万元")
    print(f"测试集 MAE:         {test_mae:.2f} 万元")
    print(f"测试集 解释方差:     {test_evs:.4f}")

    # --- 6. 交叉验证 ---
    print("\n" + "-" * 40)
    print("           5折交叉验证")
    print("-" * 40)
    cv = KFold(n_splits=5, shuffle=True, random_state=random_state)
    cv_scores = cross_val_score(lr, X_scaled, y, cv=cv, scoring="r2")
    cv_rmse_scores = -cross_val_score(
        lr, X_scaled, y, cv=cv,
        scoring="neg_root_mean_squared_error"
    )
    print(f"5折 CV R² 均值:     {cv_scores.mean():.4f} (±{cv_scores.std():.4f})")
    print(f"5折 CV RMSE 均值:   {cv_rmse_scores.mean():.2f} 万元")

    # --- 7. 特征重要性（系数分析）---
    print("\n" + "-" * 40)
    print("        特征系数 TOP-15（按绝对值排序）")
    print("-" * 40)

    coef_df = pd.DataFrame({
        "特征": feature_names,
        "系数": lr.coef_,
        "系数绝对值": np.abs(lr.coef_)
    }).sort_values("系数绝对值", ascending=False)

    print(coef_df.head(15).to_string(index=False))

    # --- 8. 绘制评估图表 ---
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    fig.suptitle("上海徐家汇区域二手房价格线性回归模型评估", fontsize=16, fontweight="bold")

    # 8.1 预测值 vs 真实值（测试集）
    ax1 = axes[0, 0]
    ax1.scatter(y_test, y_test_pred, alpha=0.5, edgecolors="k", linewidth=0.3)
    ax1.plot([y_test.min(), y_test.max()], [y_test.min(), y_test.max()], "r--", lw=2)
    ax1.set_xlabel("真实价格（万元）")
    ax1.set_ylabel("预测价格（万元）")
    ax1.set_title(f"预测 vs 真实 (测试集 R²={test_r2:.3f})")
    ax1.grid(True, alpha=0.3)

    # 8.2 残差图
    ax2 = axes[0, 1]
    residuals = y_test - y_test_pred
    ax2.scatter(y_test_pred, residuals, alpha=0.5, edgecolors="k", linewidth=0.3)
    ax2.axhline(y=0, color="r", linestyle="--", lw=2)
    ax2.set_xlabel("预测价格（万元）")
    ax2.set_ylabel("残差（万元）")
    ax2.set_title("残差图")
    ax2.grid(True, alpha=0.3)

    # 8.3 残差分布直方图
    ax3 = axes[0, 2]
    ax3.hist(residuals, bins=30, edgecolor="k", alpha=0.7, density=True)
    ax3.axvline(x=0, color="r", linestyle="--", lw=2)
    ax3.set_xlabel("残差（万元）")
    ax3.set_ylabel("密度")
    ax3.set_title("残差分布")

    # 8.4 特征系数条形图（TOP-10）
    ax4 = axes[1, 0]
    top10 = coef_df.head(10)
    colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in top10["系数"]]
    ax4.barh(range(len(top10)), top10["系数"].values, color=colors, edgecolor="k")
    ax4.set_yticks(range(len(top10)))
    ax4.set_yticklabels(top10["特征"].values, fontsize=9)
    ax4.set_xlabel("系数值")
    ax4.set_title("TOP-10 特征系数（标准化后）")
    ax4.axvline(x=0, color="black", lw=0.8)
    ax4.invert_yaxis()
    ax4.grid(True, alpha=0.3, axis="x")

    # 8.5 交叉验证R²分布
    ax5 = axes[1, 1]
    cv_r2_list = []
    for i, (train_idx, val_idx) in enumerate(cv.split(X_scaled)):
        X_tr, X_val = X_scaled[train_idx], X_scaled[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]
        lr_cv = LinearRegression()
        lr_cv.fit(X_tr, y_tr)
        r2_val = r2_score(y_val, lr_cv.predict(X_val))
        cv_r2_list.append(r2_val)
    ax5.bar(range(1, 6), cv_r2_list, color=["#3498db"], edgecolor="k")
    ax5.axhline(y=test_r2, color="r", linestyle="--", lw=2, label=f"测试集R²={test_r2:.3f}")
    ax5.set_xlabel("折数")
    ax5.set_ylabel("R²")
    ax5.set_title("5折交叉验证 R²")
    ax5.legend()
    ax5.set_ylim(0, 1)

    # 8.6 学习曲线
    ax6 = axes[1, 2]
    train_sizes = np.linspace(0.1, 1.0, 10)
    train_scores = []
    test_scores = []
    for size in train_sizes:
        n_samples = int(size * X_train.shape[0])
        lr_lc = LinearRegression()
        lr_lc.fit(X_train[:n_samples], y_train[:n_samples])
        train_scores.append(r2_score(y_train[:n_samples], lr_lc.predict(X_train[:n_samples])))
        test_scores.append(r2_score(y_test, lr_lc.predict(X_test)))
    ax6.plot(train_sizes * X_train.shape[0], train_scores, "o-", label="训练集R²", color="#2ecc71")
    ax6.plot(train_sizes * X_train.shape[0], test_scores, "s-", label="测试集R²", color="#e74c3c")
    ax6.set_xlabel("训练样本数")
    ax6.set_ylabel("R²")
    ax6.set_title("学习曲线")
    ax6.legend()
    ax6.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("model_evaluation.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("\n评估图表已保存至: model_evaluation.png")

    return lr, scaler, coef_df, {
        "train_r2": train_r2,
        "test_r2": test_r2,
        "test_rmse": test_rmse,
        "test_mae": test_mae,
        "cv_r2_mean": cv_scores.mean(),
        "cv_rmse_mean": cv_rmse_scores.mean(),
    }


def compare_models(X, y, feature_names):
    """对比多种线性模型变体"""
    print("\n" + "=" * 60)
    print("           模型对比：Linear / Ridge / Lasso")
    print("=" * 60)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42
    )

    models = {
        "LinearRegression": LinearRegression(),
        "Ridge (alpha=1.0)": Ridge(alpha=1.0),
        "Ridge (alpha=10.0)": Ridge(alpha=10.0),
        "Lasso (alpha=0.1)": Lasso(alpha=0.1, max_iter=5000),
        "Lasso (alpha=1.0)": Lasso(alpha=1.0, max_iter=5000),
    }

    results = []
    for name, model in models.items():
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        r2 = r2_score(y_test, y_pred)
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        mae = mean_absolute_error(y_test, y_pred)
        results.append({
            "模型": name,
            "R²": round(r2, 4),
            "RMSE(万元)": round(rmse, 2),
            "MAE(万元)": round(mae, 2),
        })

    result_df = pd.DataFrame(results)
    print(result_df.to_string(index=False))
    return result_df


if __name__ == "__main__":
    print("模型训练模块已就绪")
    print("请通过 main.py 运行完整流程")