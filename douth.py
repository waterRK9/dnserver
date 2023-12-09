import httpx
import dns.message
import dns.query
import dns.rdatatype
import dns.message
import dns.resolver
import dns.quic
import dns.edns
import dns.enum
import time
import dns.rdataclass
from reslib.common import Prefs, stats, cache, RootZone
from reslib.usage import usage
from reslib.query import Query
from reslib.lookup import resolve_name
from reslib.batch import batchmode

example_urls=["ameblo.jp","yolasite.com","istockphoto.com","pinterest.com","about.me","tripod.com","a8.net","topsy.com","yale.edu","wired.com","state.gov","skyrock.com","loc.gov","goo.ne.jp","disqus.com","samsung.com","alibaba.com","dyndns.org","hp.com","altervista.org","smugmug.com","businessweek.com","guardian.co.uk","gravatar.com","so-net.ne.jp","chronoengine.com","marriott.com","wikispaces.com","mlb.com","rediff.com","va.gov","qq.com","diigo.com","imageshack.us","mayoclinic.com","yellowbook.com","aol.com","narod.ru","upenn.edu","scribd.com","harvard.edu","icq.com","senate.gov","photobucket.com","yellowpages.com","europa.eu","meetup.com","nifty.com","feedburner.com","scientificamerican.com"]
doh_resolvers=["https://dns.quad9.net/dns-query","https://dns.google/dns-query","https://cloudflare-dns.com/dns-query"]
dot_resolvers=["9.9.9.9","9.9.9.10","1.1.1.1","1.0.0.1","8.8.8.8","8.8.4.4"]
dou_resolvers=["9.9.9.9","9.9.9.10","1.1.1.1","1.0.0.1","8.8.8.8","8.8.4.4"]
def doh(query: str,resolver:str,record:str,edns: int,pad:int):
    with httpx.Client() as client:
        start_time=time.time()
        q = dns.message.make_query(query, record, use_edns=edns, pad=pad)
        r = dns.query.https(q, resolver, session=client)
        end_time=time.time()
        return r,(end_time-start_time)*1000
    
def dotls(query: str,record:str,resolver:str):
    start_time=time.time()
    q = dns.message.make_query(query, record)
    r = dns.query.tls(q, resolver)
    end_time=time.time()
    print()
    return r,(end_time-start_time)*1000


def dou(query: str,record:str,resolver:str):
    start_time=time.time()
    q = dns.message.make_query(query, record)
    r = dns.query.udp(q, resolver)
    end_time=time.time()
    return r,(end_time-start_time)*1000
    
def udp_qmin(query:str,record:str):
    start_time=time.time()
    query = Query(query, record, 'IN', minimize=Prefs.MINIMIZE)
    resolve_name(query, RootZone, addResults=query)
    end_time=time.time()
    return query,(end_time-start_time)*1000

def dot_qmin(query:str,record:str):
    start_time=time.time()
    query = Query(query, record, 'IN', minimize=Prefs.MINIMIZE)
    resolve_name(query, RootZone, addResults=query,method='http')
    end_time=time.time()
    return query,(end_time-start_time)*1000

if __name__ == "__main__":
    answers=[]
    http,tls,quic,udp,udp_q,tls_q=True,True,True,True,True,False
    record="NS"
    if http:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            for resolver in doh_resolvers:
                answer,ms=doh(url,resolver,record,0,54)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
            time.sleep(0.1)
        print("DoH impl with padding and EDNS(0)",doh_ms/cnt)
        answers.append(("DoH impl with padding and EDNS(0)",doh_ms/cnt))
    if http:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            for resolver in doh_resolvers:
                answer,ms=doh(url,resolver,record,None,None)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
            time.sleep(0.1)
        print("DoH impl",doh_ms/cnt)
        answers.append(("DoH impl",doh_ms/cnt))
    if tls:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            for resolver in dot_resolvers:
                answer,ms=dotls(url,record,resolver)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
            time.sleep(0.1)
        print("DoT impl",doh_ms/cnt)
        answers.append(("DoT impl",doh_ms/cnt))
    if udp:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            for resolver in dou_resolvers:
                answer,ms=dou(url,record,resolver)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
            time.sleep(0.1)
        print("UDP impl",doh_ms/cnt)
        answers.append(("UDP impl",doh_ms/cnt))
    if udp_q:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            try:
                answer,ms=udp_qmin(url,record)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
                time.sleep(0.1)
            except:
                continue
        print("QMin impl",doh_ms/cnt)
        answers.append(("QMin impl",doh_ms/cnt))
    if tls_q:
        doh_ms=0.0
        cnt=0
        for url in example_urls :
            try:
                answer,ms=dot_qmin(url,record)
                print(answer,ms)
                doh_ms+=ms
                cnt+=1
                time.sleep(0.1)
            except:
                continue
        print("DoT QMin impl",doh_ms/cnt)
        answers.append(("DoT QMin impl",doh_ms/cnt))
    
    print('')
    print('record:',record)
    for ans in answers:
        print(ans[0],ans[1])