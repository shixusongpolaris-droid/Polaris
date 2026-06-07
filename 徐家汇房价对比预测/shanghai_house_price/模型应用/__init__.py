"""
模型应用模块：一元/多元回归分析 + 模型训练 + 对比评估 + 可视化
"""
from .simple_regression import simple_linear_regression_analysis, plot_simple_regression
from .model_train import train_linear_regression, compare_models
from .model_comparison import cross_validate_models, plot_model_comparison
