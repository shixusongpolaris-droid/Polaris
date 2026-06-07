"""
建模数据准备 —— 从清洗后的 DataFrame 构建特征矩阵 X 和目标变量 y
"""
import numpy as np
from sklearn.preprocessing import OneHotEncoder


def prepare_model_data(df):
    """从 DataFrame 构建特征矩阵 X、目标 y、特征名列表"""
    df = df.dropna(subset=['房屋总价_万元'])

    numeric_features = [
        '建筑面积_㎡', '户型_室', '户型_厅', '户型_卫',
        '总楼层', '楼层相对位置', '房龄', '小区容积率', '小区绿化率', '物业费',
        '到最近地铁站距离', '周边1km地铁站数', '梯户比',
        '装修等级', '关注人数', '产权年限'
    ]
    binary_features = [
        '是否电梯房', '是否近地铁', '朝向_南', '朝向_南北通透',
        '是否学区房', '是否VR看房', '是否房本满五年', '是否随时看房'
    ]
    categorical_features = ['楼栋类型', '房屋用途', '楼层类别']

    for col in numeric_features + binary_features:
        if col not in df.columns:
            df[col] = 0
    for col in categorical_features:
        if col not in df.columns:
            df[col] = '未知'

    X_numeric = df[numeric_features].fillna(0).values
    X_binary = df[binary_features].fillna(0).astype(int).values

    enc = OneHotEncoder(sparse_output=False, drop='first', handle_unknown='ignore')
    X_cat = enc.fit_transform(df[categorical_features].fillna('未知'))
    cat_names = enc.get_feature_names_out(categorical_features)

    X = np.hstack([X_numeric, X_binary, X_cat])
    y = df['房屋总价_万元'].values
    feature_names = numeric_features + binary_features + list(cat_names)

    print(f"特征矩阵形状: {X.shape}，特征数: {len(feature_names)}")
    return X, y, feature_names
