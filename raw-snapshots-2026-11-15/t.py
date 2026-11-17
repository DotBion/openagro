import sys,re
from bs4 import BeautifulSoup
for f in sys.argv[1:]:
    s=BeautifulSoup(open(f,errors='ignore').read(),'html.parser')
    for x in s(['script','style','noscript','svg']): x.decompose()
    t=re.sub(r'\n\s*\n+','\n',s.get_text('\n'))
    t=re.sub(r'[ \t]+',' ',t)
    open(f+'.txt','w').write(t)
    print(f, len(t))
