"""
模型对比与可视化 —— 交叉验证、多模型评估、对比图绘制
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import cross_val_score
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import PolynomialFeatures
from sklearn.ensemble import RandomForestRegressor
try:
    from lightgbm import LGBMRegressor
    HAS_LGBM = True
except (ImportError, OSError):
    LGBMRegressor = None
    HAS_LGBM = False


def cross_validate_models(X_scaled, y_log):
    """5折交叉验证 R² 均值"""
    poly = PolynomialFeatures(degree=2, include_bias=False)
    X_poly = poly.fit_transform(X_scaled)

    cv_scores = {}
    cv_scores['线性回归'] = cross_val_score(
        LinearRegression(), X_scaled, y_log, cv=5, scoring='r2'
    ).mean()
    cv_scores['多项式回归(deg=2)'] = cross_val_score(
        Ridge(alpha=10.0), X_poly, y_log, cv=5, scoring='r2'
    ).mean()
    cv_scores['随机森林'] = cross_val_score(
        RandomForestRegressor(n_estimators=100, max_depth=15, random_state=42, n_jobs=-1),
        X_scaled, y_log, cv=5, scoring='r2'
    ).mean()
    if HAS_LGBM:
        cv_scores['LightGBM'] = cross_val_score(
            LGBMRegressor(n_estimators=100, max_depth=10, learning_rate=0.05, random_state=42, verbose=-1),
            X_scaled, y_log, cv=5, scoring='r2'
        ).mean()
    else:
        cv_scores['LightGBM'] = None
    return cv_scores


def plot_model_comparison(result_df, y_test_exp, predictions):
    """绘制模型对比图（真实价格尺度）"""
    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    fig.suptitle("四种模型性能对比", fontsize=18, fontweight='bold')

    # 图1：R²
    ax1 = axes[0, 0]
    bars = ax1.bar(result_df.index, result_df['R²'],
                   color=['#3498db', '#2ecc71', '#e74c3c', '#9b59b6'][:len(result_df)])
    ax1.set_ylabel('R²')
    ax1.set_title('测试集 R² 对比')
    ax1.set_ylim(0, 1)
    for bar, val in zip(bars, result_df['R²']):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.01,
                 f'{val:.3f}', ha='center', fontsize=9)

    # 图2：RMSE
    ax2 = axes[0, 1]
    bars = ax2.bar(result_df.index, result_df['RMSE(万元)'],
                   color=['#3498db', '#2ecc71', '#e74c3c', '#9b59b6'][:len(result_df)])
    ax2.set_ylabel('RMSE（万元）')
    ax2.set_title('测试集 RMSE 对比')
    for bar, val in zip(bars, result_df['RMSE(万元)']):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.01,
                 f'{val:.1f}', ha='center', fontsize=9)

    # 图3：最佳模型预测 vs 真实
    ax3 = axes[1, 0]
    best_model = result_df['R²'].idxmax()
    y_pred_best = predictions[best_model]
    ax3.scatter(y_test_exp, y_pred_best, alpha=0.5, edgecolors='k', linewidth=0.3)
    ax3.plot([y_test_exp.min(), y_test_exp.max()],
             [y_test_exp.min(), y_test_exp.max()], 'r--', lw=2)
    ax3.set_xlabel('真实价格（万元）')
    ax3.set_ylabel('预测价格（万元）')
    ax3.set_title(f'{best_model} 预测 vs 真实 (R²={result_df.loc[best_model, "R²"]:.3f})')
    ax3.grid(alpha=0.3)

    # 图4：残差分布（最佳模型）
    ax4 = axes[1, 1]
    residuals = y_test_exp - y_pred_best
    ax4.hist(residuals, bins=25, edgecolor='k', alpha=0.7, density=True)
    ax4.axvline(0, color='r', linestyle='--', lw=2)
    ax4.set_xlabel('残差（万元）')
    ax4.set_title(f'{best_model} 残差分布')

    plt.tight_layout()
    plt.savefig("model_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()
