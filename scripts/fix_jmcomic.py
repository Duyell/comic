"""JMComic 库补丁脚本 - 修复 description 字段解析失败的问题

使用方式:
  python scripts/fix_jmcomic.py
"""
import jmcomic.jm_toolkit
p = jmcomic.jm_toolkit.__file__

with open(p, encoding='utf-8') as f:
    c = f.read()

old = "pattern_html_album_description = compile(r'叙述：([\\s\\S]*?)</h2>')"
new = "pattern_html_album_description = (compile(r'叙述：([\\s\\S]*?)</h2>'), '')"
c = c.replace(old, new)

with open(p, 'w', encoding='utf-8') as f:
    f.write(c)

print(f'补丁已应用到: {p}')
