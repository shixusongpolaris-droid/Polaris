#include <iostream>
#include <vector>
#include <string>

using namespace std;

// 构建 next 数组（部分匹配表）
vector<int> buildNext(const string& pattern) {
    int m = pattern.size();
    vector<int> next(m);
    next[0] = 0;
    int j = 0;

    for (int i = 1; i < m; i++) {
        while (j > 0 && pattern[i] != pattern[j]) {
            j = next[j - 1];
        }
        if (pattern[i] == pattern[j]) {
            j++;
        }
        next[i] = j;
    }
    return next;
}

// KMP 搜索，返回所有匹配位置
vector<int> kmpSearch(const string& text, const string& pattern) {
    vector<int> result;
    if (pattern.empty()) return result;

    int n = text.size(), m = pattern.size();
    vector<int> next = buildNext(pattern);

    int j = 0;
    for (int i = 0; i < n; i++) {
        while (j > 0 && text[i] != pattern[j]) {
            j = next[j - 1];
        }
        if (text[i] == pattern[j]) {
            j++;
        }
        if (j == m) {
            result.push_back(i - m + 1);
            j = next[j - 1];
        }
    }
    return result;
}

int main() {
    string text, pattern;

    cout << "====== KMP 字符串匹配算法 ======" << endl;
    cout << "请输入主串 text: ";
    getline(cin, text);
    cout << "请输入模式串 pattern: ";
    getline(cin, pattern);

    vector<int> matches = kmpSearch(text, pattern);

    if (matches.empty()) {
        cout << "未找到匹配位置。" << endl;
    } else {
        cout << "匹配位置: [";
        for (size_t i = 0; i < matches.size(); i++) {
            cout << matches[i];
            if (i < matches.size() - 1) cout << ", ";
        }
        cout << "]" << endl;
    }

    return 0;
}
