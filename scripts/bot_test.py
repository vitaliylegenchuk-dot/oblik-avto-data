import sys;sys.path.insert(0,"scripts");import bot
s,u=bot.fs();print("urgent:",bot.urgent(s,u))
base=u.rsplit("/documents/",1)[0]+"/documents"
r=s.get(u,params={"pageSize":50,"mask.fieldPaths":"n"});print("docs:",[d["name"].rsplit("/",1)[-1] for d in r.json().get("documents",[])])
