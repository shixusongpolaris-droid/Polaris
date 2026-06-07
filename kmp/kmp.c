#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 构建 next 数组（部分匹配表）
int* buildNext(const char* pattern, int m) {
    int* next = (int*)malloc(m * sizeof(int));
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

// KMP 搜索，返回所有匹配位置，结果存入 matches，返回匹配数量
int kmpSearch(const char* text, const char* pattern, int* matches) {
    int n = strlen(text);
    int m = strlen(pattern);
    if (m == 0) return 0;

    int* next = buildNext(pattern, m);
    int count = 0;
    int j = 0;

    for (int i = 0; i < n; i++) {
        while (j > 0 && text[i] != pattern[j]) {
            j = next[j - 1];
        }
        if (text[i] == pattern[j]) {
            j++;
        }
        if (j == m) {
            matches[count++] = i - m + 1;
            j = next[j - 1];
        }
    }

    free(next);
    return count;
}

int main() {
    char text[1024];
    char pattern[256];

    printf("====== KMP 字符串匹配算法 ======\n");
    printf("请输入主串 text: ");
    fgets(text, sizeof(text), stdin);
    text[strcspn(text, "\n")] = '\0';

    printf("请输入模式串 pattern: ");
    fgets(pattern, sizeof(pattern), stdin);
    pattern[strcspn(pattern, "\n")] = '\0';

    int n = strlen(text);
    int* matches = (int*)malloc(n * sizeof(int));
    int count = kmpSearch(text, pattern, matches);

    if (count == 0) {
        printf("未找到匹配位置。\n");
    } else {
        printf("匹配位置: [");
        for (int i = 0; i < count; i++) {
            printf("%d", matches[i]);
            if (i < count - 1) printf(", ");
        }
        printf("]\n");
    }

    free(matches);
    return 0;
}
