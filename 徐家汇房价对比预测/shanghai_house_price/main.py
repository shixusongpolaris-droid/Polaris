"""
上海徐家汇商圈（≈10km²）二手房价格多模型对比
模型：一元线性回归 / 多元线性回归 / 多项式回归(deg=2) / 随机森林 / LightGBM

项目结构：
  数据采集/    — 爬虫 + CSV解析 + 模拟数据
  特征工程/    — 预处理 + 特征构造 + 建模数据准备
  模型应用/    — 回归分析 + 模型训练 + 对比评估 + 可视化
"""
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import StandardScaler, PolynomialFeatures
from sklearn.ensemble import RandomForestRegressor
try:
    from lightgbm import LGBMRegressor
    HAS_LGBM = True
except (ImportError, OSError):
    LGBMRegressor = None
    HAS_LGBM = False
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
import warnings
warnings.filterwarnings("ignore")

# ---- 三模块导入 ----
from 数据采集 import load_and_parse_csv
from 特征工程 import HouseDataPreprocessor, prepare_model_data
from 模型应用 import plot_simple_regression, cross_validate_models, plot_model_comparison

# ============= 字体设置（避免 R² 显示为方框） =============
plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "Heiti TC", "Songti SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["mathtext.fontset"] = "dejavusans"


def main():
    print("=" * 60)
    print("  上海徐家汇商圈（≈10km²）二手房价格多模型对比")
    print("=" * 60)

    # 1. 确定 CSV 路径（自动发现 lianjia.csv）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, "lianjia.csv")
    if not os.path.isfile(csv_path):
        csv_path = input("请输入 lianjia.csv 的完整路径: ").strip().strip('"')
    if not os.path.isfile(csv_path):
        print(f"文件不存在: {csv_path}")
        return

    # ---- 板块一：数据采集 ----
    df = load_and_parse_csv(csv_path)

    # ---- 板块二：特征工程 ----
    preprocessor = HouseDataPreprocessor()
    df = preprocessor.engineer_features(df)
    X, y, feat_names = prepare_model_data(df)

    # ---- 板块三：模型应用 ----
    # 3.1 一元线性回归分析（逐特征单独拟合，使用原始价格）
    print("\n" + "=" * 60)
    print("  一元线性回归分析（逐特征）")
    print("=" * 60)
    simple_lr_results = plot_simple_regression(X, y, feat_names, top_n=12)
    print("\n===== 一元线性回归结果（按R²降序，取前15）=====")
    pd.set_option('display.max_colwidth', 20)
    pd.set_option('display.width', 160)
    print(simple_lr_results.head(15).to_string(
        float_format=lambda x: f'{x:.4f}' if abs(x) < 10 else f'{x:.2f}'
    ))
    print(f"\n解读：R² 越接近 1，该特征单独解释房价的能力越强。")
    print(f"      p值 < 0.05(*) 表示统计显著，< 0.01(**) 表示高度显著，< 0.001(***) 表示极显著。\n")

    # 3.2 对数变换 y，标准化 X
    y_log = np.log(y)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 3.3 划分训练/测试集
    X_train, X_test, y_train_log, y_test_log = train_test_split(
        X_scaled, y_log, test_size=0.2, random_state=42
    )
    print(f"训练集: {X_train.shape[0]} 条，测试集: {X_test.shape[0]} 条")

    # 3.4 训练多种模型
    results = []
    predictions_exp = {}

    # ----- 多元线性回归 -----
    lr = LinearRegression()
    lr.fit(X_train, y_train_log)
    pred_lr = np.exp(lr.predict(X_test))
    results.append({
        '模型': '线性回归',
        'R²': r2_score(y_test_log, np.log(pred_lr)),
        'RMSE(万元)': np.sqrt(mean_squared_error(np.exp(y_test_log), pred_lr)),
        'MAE(万元)': mean_absolute_error(np.exp(y_test_log), pred_lr)
    })
    predictions_exp['线性回归'] = pred_lr

    # ----- 多项式回归 (degree=2, Ridge) -----
    poly = PolynomialFeatures(degree=2, include_bias=False)
    X_train_poly = poly.fit_transform(X_train)
    X_test_poly = poly.transform(X_test)
    ridge = Ridge(alpha=10.0)
    ridge.fit(X_train_poly, y_train_log)
    pred_poly = np.exp(ridge.predict(X_test_poly))
    results.append({
        '模型': '多项式回归(deg=2)',
        'R²': r2_score(y_test_log, np.log(pred_poly)),
        'RMSE(万元)': np.sqrt(mean_squared_error(np.exp(y_test_log), pred_poly)),
        'MAE(万元)': mean_absolute_error(np.exp(y_test_log), pred_poly)
    })
    predictions_exp['多项式回归(deg=2)'] = pred_poly

    # ----- 随机森林 -----
    rf = RandomForestRegressor(n_estimators=200, max_depth=15, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train_log)
    pred_rf = np.exp(rf.predict(X_test))
    results.append({
        '模型': '随机森林',
        'R²': r2_score(y_test_log, np.log(pred_rf)),
        'RMSE(万元)': np.sqrt(mean_squared_error(np.exp(y_test_log), pred_rf)),
        'MAE(万元)': mean_absolute_error(np.exp(y_test_log), pred_rf)
    })
    predictions_exp['随机森林'] = pred_rf

    # ----- LightGBM (可选) -----
    if HAS_LGBM:
        lgb = LGBMRegressor(n_estimators=200, max_depth=10, learning_rate=0.05, random_state=42, verbose=-1)
        lgb.fit(X_train, y_train_log)
        pred_lgb = np.exp(lgb.predict(X_test))
        results.append({
            '模型': 'LightGBM',
            'R²': r2_score(y_test_log, np.log(pred_lgb)),
            'RMSE(万元)': np.sqrt(mean_squared_error(np.exp(y_test_log), pred_lgb)),
            'MAE(万元)': mean_absolute_error(np.exp(y_test_log), pred_lgb)
        })
        predictions_exp['LightGBM'] = pred_lgb
    else:
        print('  [跳过] LightGBM (缺少 libomp OpenMP 库)')

    # 3.5 输出对比表格
    result_df = pd.DataFrame(results).set_index('模型')
    print("\n===== 四种模型对比（目标变量已取对数，误差已还原为万元）=====")
    print(result_df.to_string(float_format=lambda x: f"{x:.4f}" if x < 10 else f"{x:.2f}"))

    best_model = result_df['R²'].idxmax()
    print(f"\n最佳模型：{best_model}（R²={result_df.loc[best_model, 'R²']:.4f}）")

    # 3.6 交叉验证
    print("\n===== 5折交叉验证 R² 均值 =====")
    cv_scores = cross_validate_models(X_scaled, y_log)
    for name, score in cv_scores.items():
        if score is not None:
            print(f"{name}: {score:.4f}")
        else:
            print(f"{name}: 不可用")

    # 3.7 可视化
    plot_model_comparison(result_df, np.exp(y_test_log), predictions_exp)
    print("\n图表已保存为 model_comparison.png")
    print("✅ 全部任务完成！")


if __name__ == "__main__":
    main()
