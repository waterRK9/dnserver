"""
Main setup of functions to perform iterative DNS resolution.

"""

import time

import dns.message
import dns.query
import dns.rdatatype
import dns.rcode
import dns.dnssec

from reslib.common import Prefs, cache, stats, \
    MAX_CNAME, MAX_QUERY, MAX_DELEG
from reslib.zone import Zone
from reslib.query import Query
from reslib.utils import make_query, send_query, is_referral


def get_ns_addrs(zone, message):
    """
    Populate nameserver addresses for zone from a given referral message.

    By default, we only save and use NS record addresses we can find
    in the additional section of the referral. To additonally resolve
    all non-glue NS record addresses, we need to supply the -n (NSRESOLVE)
    switch to this program. If no NS address records can be found in the
    additional section of the referral, we switch to NSRESOLVE mode.
    """

    needsGlue = []
    for nsname in zone.nslist:
        if nsname.is_subdomain(zone.name):
            needsGlue.append(nsname)
    needToResolve = list(set(zone.nslist) - set(needsGlue))

    for rrset in message.additional:
        if rrset.rdtype in [dns.rdatatype.A, dns.rdatatype.AAAA]:
            name = rrset.name
            for rr in rrset:
                if not zone.has_ns(name):
                    continue
                if (not Prefs.NSRESOLVE) or (name in needsGlue):
                    nsobj = cache.get_ns(name)
                    nsobj.install_ip(rr.address)

    if not zone.iplist() or Prefs.NSRESOLVE:
        for name in needToResolve:
            nsobj = cache.get_ns(name)
            if nsobj.iplist:
                continue
            for addrtype in ['A', 'AAAA']:
                nsquery = Query(name, addrtype, 'IN', Prefs.MINIMIZE,
                                is_nsquery=True)
                nsquery.quiet = True
                resolve_name(nsquery, cache.closest_zone(nsquery.qname),
                             inPath=False)
                for ip in nsquery.get_answer_ip_list():
                    nsobj.install_ip(ip)

    return


def process_referral(message, query):
    """
    Process referral. Returns a zone object for the referred zone.
    """

    for rrset in message.authority:
        if rrset.rdtype == dns.rdatatype.NS:
            break
    else:
        print("ERROR: unable to find NS RRset in referral response")
        return None

    zonename = rrset.name
    if Prefs.VERBOSE and not query.quiet:
        print(">>        [Got Referral to zone: %s in %.3f s]" % \
              (zonename, query.elapsed_last))

    zone = cache.get_zone(zonename)
    if zone is None:
        zone = Zone(zonename, cache)
        for rr in rrset:
            _ = zone.install_ns(rr.target)

    get_ns_addrs(zone, message)

    if Prefs.VERBOSE and not query.quiet:
        zone.print_details()

    return zone


def process_answer(response, query, addResults=None):
    """
    Process answer section, chasing aliases when needed.
    """

    cname_dict = {}              # dict of alias -> target

    # If minimizing, ignore answers for intermediate query names.
    if query.qname != query.orig_qname:
        return

    if Prefs.VERBOSE and not query.quiet:
        print(">>        [Got answer in  %.3f s]" % query.elapsed_last)

    if not response.answer:
        if not query.quiet:
            print("ERROR: NODATA: %s of type %s not found" % \
                  (query.qname, query.qtype))
        return

    for rrset in response.answer:
        if rrset.rdtype == dns.rdatatype.from_text(query.qtype) and \
           rrset.name == query.qname:
            query.answer_rrset.append(rrset)
            if addResults:
                addResults.full_answer_rrset.append(rrset)
            query.got_answer = True
        elif rrset.rdtype == dns.rdatatype.DNAME:
            # Add DNAME record to results. Technically a good resolver should
            # do DNAME->CNAME synthesis itself here, but we rely on the fact
            # that almost all authorities provide the CNAMEs themselves.
            query.answer_rrset.append(rrset)
            if addResults:
                addResults.full_answer_rrset.append(rrset)
            if Prefs.VERBOSE:
                print(rrset.to_text())
        elif rrset.rdtype == dns.rdatatype.CNAME:
            query.answer_rrset.append(rrset)
            if addResults:
                addResults.full_answer_rrset.append(rrset)
            if Prefs.VERBOSE:
                print(rrset.to_text())
            cname = rrset[0].target
            cname_dict[rrset.name] = rrset[0].target
            stats.cnt_cname += 1
            if stats.cnt_cname >= MAX_CNAME:
                print("ERROR: Too many (%d) CNAME indirections." % MAX_CNAME)
                return

    if cname_dict:
        final_alias = response.question[0].name
        while True:
            if final_alias in cname_dict:
                final_alias = cname_dict[final_alias]
            else:
                break
        cname_query = Query(final_alias, query.qtype, query.qclass,
                            Prefs.MINIMIZE)
        if addResults:
            addResults.cname_chain.append(cname_query)
        resolve_name(cname_query, cache.closest_zone(cname),
                     inPath=False, addResults=addResults)

    return


def process_response(response, query, addResults=None):
    """
    Process a DNS response. Returns rcode, answer message, zone referral.
    """

    rc = None
    ans = None
    referral = None

    if not response:
        return (rc, ans, referral)
    rc = response.rcode()
    query.rcode = rc
    if rc == dns.rcode.NOERROR:
        if is_referral(response):
            referral = process_referral(response, query)
            if not referral:
                print("ERROR: processing referral")
        else:                                            # Answer
            process_answer(response, query, addResults=addResults)
    elif rc == dns.rcode.NXDOMAIN:                       # NXDOMAIN
        if not query.quiet:
            print("ERROR: NXDOMAIN: %s not found" % query.qname)

    return (rc, referral)


def send_query_zone(query, zone):
    """
    Send DNS query to nameservers of given zone
    """

    response = None

    if Prefs.VERBOSE and not query.quiet:
        print("\n>> Query: %s %s %s at zone %s" % \
               (query.qname, query.qtype, query.qclass, zone.name))

    msg = make_query(query.qname, query.qtype, query.qclass)

    nsaddr_list = zone.iplist_sorted_by_rtt()
    if not nsaddr_list:
        print("ERROR: No nameserver addresses found for zone: %s." % zone.name)
        return None

    time_start = time.time()
    for nsaddr in nsaddr_list:
        if stats.cnt_query1 + stats.cnt_query2 >= MAX_QUERY:
            print("ERROR: Max number of queries (%d) exceeded." % MAX_QUERY)
            return None
        if Prefs.VERBOSE and not query.quiet:
            print(">>   Send to zone %s at address %s" % (zone.name, nsaddr.addr))
        response = send_query(msg, nsaddr, query, newid=True)
        if response:
            rc = response.rcode()
            if rc not in [dns.rcode.NOERROR, dns.rcode.NXDOMAIN]:
                stats.cnt_fail += 1
                print("WARNING: response %s from %s" % (dns.rcode.to_text(rc), nsaddr.addr))
            else:
                break
    else:
        print("ERROR: Queries to all servers for zone %s failed." % zone.name)

    query.elapsed_last = time.time() - time_start
    return response


def resolve_name(query, zone, inPath=True, addResults=None):
    """
    Resolve a DNS query. addResults is an optional Query object to
    which the answer results are to be added.
    """

    curr_zone = zone
    repeatZone = False

    while stats.cnt_deleg < MAX_DELEG:

        if query.minimize:
            if repeatZone:
                query.prepend_label()
                repeatZone = False
            else:
                query.set_minimized(curr_zone)

        response = send_query_zone(query, curr_zone)
        if not response:
            return

        rc, referral = process_response(response, query, addResults=addResults)

        if rc == dns.rcode.NXDOMAIN:
            # for broken servers that give NXDOMAIN for empty non-terminals
            if Prefs.VIOLATE and (query.minimize) and (query.qname != query.orig_qname):
                repeatZone = True
            else:
                break

        if not referral:
            if (not query.minimize) or (query.qname == query.orig_qname):
                break
            elif query.minimize:
                repeatZone = True
        else:
            stats.cnt_deleg += 1
            if inPath:
                stats.delegation_depth += 1
            if not referral.name.is_subdomain(curr_zone.name):
                print("ERROR: Upward referral: %s is not subdomain of %s" %
                      (referral.name, curr_zone.name))
                break
            curr_zone = referral

    if stats.cnt_deleg >= MAX_DELEG:
        print("ERROR: Max levels of delegation (%d) reached." % MAX_DELEG)

    return
