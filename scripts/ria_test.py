import json,os,sys,urllib.request
K=os.environ["RIA_KEY"]
def g(p):
    try:
        with urllib.request.urlopen("https://developers.ria.com"+p+("&" if "?" in p else "?")+"api_key="+K,timeout=30) as r: return json.load(r)
    except Exception as e: return str(e)
for c in (6,7):
    r_=g(f"/auto/categories/{c}/marks/84/models"); print(c,[m for m in r_ if "LT" in m["name"].upper()] if isinstance(r_,list) else r_)
for q in ["main_category=1&marka_id=84&model_id=35399&yers=2006&yers=2006&fuel_id=2",
          "marka_id=84&model_id=35399&yers=2006&yers=2006&fuel_id=2",
          "marka_id=84&model_id=35399&yers=2006&yers=2006",
          "marka_id=84&model_id=35399&yers=2005&yers=2007"]:
    r=g("/auto/average_price?"+q); print(q, r if isinstance(r,str) else {k:r.get(k) for k in ("total","interQuartileMean","arithmeticMean")})
