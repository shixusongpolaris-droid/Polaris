"""
一元线性回归分析 —— 每个特征单独拟合 价格 ~ 单个特征
输出 R²、系数、p值，并绘制 Top-N 散点图 + 回归线
"""
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib.pyplot as plt


def simple_linear_regression_analysis(X, y, feature_names):
    """一元线性回归：每个特征单独拟合 价格 ~ 单个特征，输出 R²、系数、p值"""
    results = []
    for i, name in enumerate(feature_names):
        x = X[:, i]
        mask = ~np.isnan(x) & ~np.isnan(y)
        x_clean = x[mask]
        y_clean = y[mask]

        if len(x_clean) < 10 or np.std(x_clean) < 1e-8:
            continue

        X_design = np.column_stack([np.ones(len(x_clean)), x_clean])
        beta = np.linalg.lstsq(X_design, y_clean, rcond=None)[0]
        y_pred = X_design @ beta

        ss_res = np.sum((y_clean - y_pred) ** 2)
        ss_tot = np.sum((y_clean - np.mean(y_clean)) ** 2)
        r2 = 1 - ss_res / ss_tot
        rmse = np.sqrt(ss_res / len(x_clean))
        mae = np.mean(np.abs(y_clean - y_pred))

        residuals = y_clean - y_pred
        sigma2 = ss_res / max(len(x_clean) - 2, 1)
        try:
            XtX_inv = np.linalg.inv(X_design.T @ X_design)
            se = np.sqrt(np.diag(XtX_inv) * sigma2)
            t_stat = beta[1] / se[1] if se[1] > 1e-12 else 0
            p_value = 2 * stats.t.sf(abs(t_stat), df=max(len(x_clean) - 2, 1))
        except np.linalg.LinAlgError:
            p_value = 1.0

        sig = '***' if p_value < 0.001 else ('**' if p_value < 0.01 else ('*' if p_value < 0.05 else ''))
        results.append({
            '特征': name, 'R²': r2, '截距(万元)': beta[0], '系数': beta[1],
            'RMSE(万元)': rmse, 'MAE(万元)': mae, 'p值': p_value, '显著性': sig
        })

    return pd.DataFrame(results).set_index('特征').sort_values('R²', ascending=False)


def plot_simple_regression(X, y, feature_names, top_n=12):
    """绘制一元线性回归散点图 + 回归线（按R²取前top_n个特征）"""
    results_df = simple_linear_regression_analysis(X, y, feature_names)
    top_features = results_df.head(top_n)

    n_cols = 3
    n_rows = (top_n + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(16, 4.5 * n_rows))
    axes = axes.flatten() if n_rows > 1 else ([axes] if n_cols == 1 else axes)

    for idx, (feat_name, row) in enumerate(top_features.iterrows()):
        ax = axes[idx]
        i = list(feature_names).index(feat_name)
        x = X[:, i]
        mask = ~np.isnan(x) & ~np.isnan(y)
        x_clean, y_clean = x[mask], y[mask]

        ax.scatter(x_clean, y_clean, alpha=0.35, s=15, edgecolors='none')

        X_design = np.column_stack([np.ones(len(x_clean)), x_clean])
        beta = np.linalg.lstsq(X_design, y_clean, rcond=None)[0]
        x_line = np.linspace(x_clean.min(), x_clean.max(), 100)
        y_line = beta[0] + beta[1] * x_line
        ax.plot(x_line, y_line, 'r-', lw=2)

        ax.set_xlabel(feat_name, fontsize=9)
        ax.set_ylabel('总价(万元)', fontsize=9)
        ax.set_title(f'R²={row["R²"]:.4f}  β={row["系数"]:.2f} {row["显著性"]}', fontsize=10)
        ax.grid(alpha=0.2)

    for j in range(idx + 1, len(axes)):
        axes[j].set_visible(False)

    plt.suptitle('一元线性回归：各特征对房价的单独解释力（按R²降序）', fontsize=14, fontweight='bold', y=1.01)
    plt.tight_layout()
    plt.savefig('simple_regression.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("\n一元回归散点图已保存为 simple_regression.png")
    return results_df
