"""Exclude women's competitions from the production scanner and cached cards."""
import re
WOMEN=re.compile(r"women|female|feminina|feminino|ignis\s+cup|equal\s+esports|girlgamer",re.I)
def excluded(row):
    values=[row.get(k,'') for k in ('league','tournament','competition','gender')]
    md=row.get('metadata') or {};values.extend(md.get(k,'') for k in ('league','title','gender'))
    return any(WOMEN.search(str(v)) for v in values)
