"""Count words the way the hosts described: everything judged (title, abstract, body,
tables, captions) except figures and the reference list.

usage: python scripts/wordcount.py writeup.md
"""
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
text = re.split(r"^##\s+References\b", text, flags=re.M)[0]
text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)        # comments
text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)            # images
text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)         # links -> text
text = re.sub(r"^\s*\|?\s*:?-{3,}.*$", " ", text, flags=re.M)  # table rules
words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.%/+-]*", text)
print(len(words))
