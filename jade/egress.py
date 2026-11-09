"""W-55 (docs/05 T22): does one simple command send a file, standard input or a variable to a network destination?

`sends_out(bare)` reads one segment of the shell classifier (`shellnorm.segments_cwd`, launcher prefixes already taken off) and answers for `curl`, `wget`
and the `nc` family. A segment that does is given the marker `MARK`, which rule R-21 matches (the same route as `git:unsafe-config` and R-15), so the rule
text in `policies/risk.yaml` stays a plain pattern and the parsing that a pattern cannot do lives here.

What counts as sent: a `curl` body that reads a file or standard input (`-d @f`, `--data-binary @-`, `--json @f`, `--data-urlencode name@f`,
`-F f=@path`, `-F f=<path`, `-T f`) or is a bare variable or the result of a command substitution (`-d "$TOKEN"`, `-d key=$TOKEN`); a `wget` `--post-file` or
`--body-file`, or `--post-data`/`--body-data` that is a bare variable; any `nc`, `ncat` or `netcat` that is not a port scan (`-z`). A body that is typed in
the command (`-d '{"a":1}'`), `--data-raw @f` (curl sends the text, not the file), `--form-string`, a header (`-H "Authorization: Bearer $T"`) and a download are
not. A send whose every destination is this machine (`localhost`, `*.localhost`, `*.test`, a loopback address) is not either; a destination
that cannot be read (`$URL`, a quoted word the segmenter blanked, a missing one) counts as remote, so every doubt is resolved toward the rule.

Nothing is executed or resolved: a name is compared as text. Limits, written down in docs/05 T22: a value in quotes that holds a space is blanked by the
segmenter (`-F "f=@a b.txt"`), a client this module does not name (`openssl s_client`, `socat`, `telnet`, `aws s3 cp`, an interpreter's socket code) and a
send assembled at run time are not seen.

Review fixes (the same decision, narrower exemptions): a destination is judged by the URL word only when nothing reroutes the connection. `-x/--proxy`, `--preproxy`,
`--socks*`, `--connect-to`, `--resolve`, `--unix-socket`, `-K/--config` (a config file or stdin can hold anything), `nc -x`, `wget -e ...proxy...`, a proxy environment
variable in the command (`HTTPS_PROXY=...`, `export http_proxy=...`) and a proxying launcher (`proxychains`, `torsocks`) make the destination not provably local. A variable
anywhere in a body is a send, not only a body that is one variable (`-d "token=$T&x=1"`). `-H @file`, `--url-query name@file` and `--variable name@file` / `%ENV`
read a file or the environment and are sends. A launcher the shell classifier does not strip (`setsid`, `stdbuf`, `flock`, `watch`, `busybox`, `parallel`, ...) is looked through.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

MARK = "egress:upload "   # prefix of the copy of a segment that R-21 matches

_VAR = re.compile(r"(?<!\\)\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*|\d)")   # `$T`, `${T}`, `$1` anywhere in a value; `$X` is how the segmenter writes a substitution
PROXY_ENV = re.compile(r"(?<![\w.-])(?:all|https?|ftps?|socks[45]?)_proxy=", re.I)   # a proxy variable assigned in the command (`NO_PROXY=` switches a proxy off and is not one)
_PROXYING_LAUNCHERS = frozenset(("proxychains", "proxychains4", "proxychains-ng", "torsocks", "torify", "tsocks"))
_LAUNCHERS = frozenset("""setsid stdbuf unbuffer arch ionice flock lockf watch doas chroot script parallel busybox taskset chrt numactl strace ltrace dtruss unshare nsenter
    runuser systemd-run""".split()) | _PROXYING_LAUNCHERS   # launchers shellnorm.PREFIX does not strip: the client is the first client-named word after one
_CLIENTS = frozenset(("curl", "wget", "nc", "ncat", "netcat"))
# curl options that change where the connection goes (a local-looking URL is then no evidence), and the ones that read a file or the environment
_CURL_ROUTE_LONG = frozenset(("proxy", "proxy1.0", "preproxy", "socks4", "socks4a", "socks5", "socks5-hostname", "connect-to", "resolve", "unix-socket", "abstract-unix-socket",
                              "config"))
_CURL_ROUTE_SHORT = frozenset("xK")
_REDIRECT = re.compile(r"^\d*[<>]")

_CURL_SHORT_VALUE = frozenset("AbcCdDeEFHKmoPQrtTuUwXxyYz")
_CURL_LONG_VALUE = frozenset("""
    abstract-unix-socket alt-svc aws-sigv4 cacert capath cert cert-type ciphers config connect-timeout connect-to continue-at cookie cookie-jar
    create-file-mode crlfile curves data data-ascii data-binary data-raw data-urlencode delegation dns-interface dns-ipv4-addr dns-ipv6-addr dns-servers
    doh-url dump-header engine expect100-timeout form form-string ftp-account ftp-alternative-to-user ftp-method ftp-port happy-eyeballs-timeout-ms
    haproxy-clientip header hostpubmd5 hostpubsha256 hsts interface ipfs-gateway json keepalive-time key key-type krb libcurl limit-rate local-port
    login-options mail-auth mail-from mail-rcpt max-filesize max-redirs max-time netrc-file noproxy oauth2-bearer output output-dir parallel-max pass
    pinnedpubkey preproxy proto proto-default proto-redir proxy proxy-cacert proxy-capath proxy-cert proxy-cert-type proxy-ciphers proxy-crlfile
    proxy-header proxy-key proxy-key-type proxy-pass proxy-pinnedpubkey proxy-service-name proxy-tls13-ciphers proxy-tlsauthtype proxy-tlspassword
    proxy-tlsuser proxy-user pubkey quote random-file range rate referer request request-target resolve retry retry-delay retry-max-time sasl-authzid
    service-name socks4 socks4a socks5 socks5-hostname speed-limit speed-time stderr telnet-option time-cond tftp-blksize tls-max tls13-ciphers
    tlsauthtype tlspassword tlsuser trace trace-ascii trace-config unix-socket upload-file url url-query user user-agent variable write-out""".split())
_CURL_DATA = {"d": "file", "data": "file", "data-ascii": "file", "data-binary": "file", "json": "file", "data-raw": "var", "data-urlencode": "urlencode",
              "F": "form", "form": "form", "form-string": "var", "T": "always", "upload-file": "always", "H": "header", "header": "header", "url-query": "urlquery",
              "variable": "variable"}

_WGET_SHORT_VALUE = frozenset("OoaPeUtTwiBQlARDXI")
_WGET_LONG_VALUE = frozenset("""
    output-document output-file append-output directory-prefix execute user-agent tries timeout wait waitretry random-wait input-file base quota level
    accept reject domains exclude-domains include-directories exclude-directories header user password http-user http-password proxy-user proxy-password
    referer method load-cookies save-cookies certificate ca-certificate private-key bind-address limit-rate dns-timeout connect-timeout read-timeout
    post-data post-file body-data body-file""".split())

_NC_SHORT_VALUE = frozenset("bDeIiMmOPpqsTVwXxZ")


def _is_var(value: str) -> bool:
    """A variable (or a command substitution, which the segmenter writes as `$X`) anywhere in the value."""
    return bool(_VAR.search(value))


def _body_is_sent(kind: str, value: str) -> bool:
    if kind == "always":
        return True
    if kind == "var":
        return _is_var(value)
    if kind == "header":        # `-H @file` sends the file's lines as headers; a typed header or one with a variable is not matched (`-H "Authorization: Bearer $T"`)
        return value.startswith("@")
    if kind == "urlquery":      # `--url-query [+]name@file`, `[+]@file` read a file into the URL; `name=$VAR` is a variable
        v = value.lstrip("+")
        return v.startswith("@") or bool(re.match(r"[^=@]*@", v)) or _is_var(v)
    if kind == "variable":      # `--variable name@file` and `--variable %ENV` read a file or the environment for `--expand-*`
        return value.startswith("%") or bool(re.match(r"[^=@%]*@", value)) or _is_var(value)
    if kind == "urlencode":     # `@file`, `name@file` (reads the file), a variable
        return value.startswith("@") or bool(re.match(r"[^=@]*@", value)) or _is_var(value)
    if kind == "form":          # `name=@file`, `name=<file`, a leading `@file`, a variable
        return value.startswith(("@", "<")) or "=@" in value or "=<" in value or _is_var(value)
    return value.startswith("@") or _is_var(value)


def is_local(dest: str) -> bool:
    """Is this destination word this machine, judged by the host the word names (never by a substring: `localhost.example.org`, `?h=localhost` and
    `localhost@example.org` are not)?"""
    d = dest.strip("\"'")
    if not d or "\\" in d or "$" in d or "\x06" in d or any(c.isspace() for c in d):
        return False
    try:
        host = urlsplit(d if "://" in d else "//" + d).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".test")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback


def _without_redirects(words: list[str]) -> list[str]:
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif _REDIRECT.match(w):
            skip = bool(re.fullmatch(r"\d*(?:<<?<?|>>?|>&|<&|&>>?)", w))   # a bare operator takes the next word as its target
        else:
            out.append(w)
    return out


def _curl(words: list[str], routed: bool = False) -> bool:
    sends, dests, i = False, [], 0
    while i < len(words):
        w = words[i]
        i += 1
        if w == "--":
            dests += words[i:]
            break
        if w.startswith("--") and len(w) > 2:
            name, eq, value = w[2:].partition("=")
            if name in _CURL_LONG_VALUE and not eq and i < len(words):
                value, i = words[i], i + 1
            if name in _CURL_ROUTE_LONG:
                routed = True
            if name == "url":
                dests.append(value)
            elif name in _CURL_DATA and _body_is_sent(_CURL_DATA[name], value):
                sends = True
        elif w.startswith("-") and len(w) > 1:
            for k, c in enumerate(w[1:], 1):
                if c in _CURL_SHORT_VALUE:
                    value = w[k + 1:]
                    if not value and i < len(words):
                        value, i = words[i], i + 1
                    if c in _CURL_ROUTE_SHORT:
                        routed = True
                    if c in _CURL_DATA and _body_is_sent(_CURL_DATA[c], value):
                        sends = True
                    break
        else:
            dests.append(w)
    return sends and (routed or not (dests and all(is_local(d) for d in dests)))


def _wget(words: list[str], routed: bool = False) -> bool:
    sends, dests, i = False, [], 0
    while i < len(words):
        w = words[i]
        i += 1
        if w == "--":
            dests += words[i:]
            break
        if w.startswith("--") and len(w) > 2:
            name, eq, value = w[2:].partition("=")
            if name in _WGET_LONG_VALUE and not eq and i < len(words):
                value, i = words[i], i + 1
            if name == "execute" and "proxy" in value.lower():
                routed = True
            if name in ("post-file", "body-file") or (name in ("post-data", "body-data") and _is_var(value)):
                sends = True
        elif w.startswith("-") and len(w) > 1:
            for k, c in enumerate(w[1:], 1):
                if c in _WGET_SHORT_VALUE:
                    value = w[k + 1:]
                    if not value and i < len(words):
                        value, i = words[i], i + 1
                    if c == "e" and "proxy" in value.lower():
                        routed = True
                    break
        else:
            dests.append(w)
    return sends and (routed or not (dests and all(is_local(d) for d in dests)))


def _nc(words: list[str], routed: bool = False) -> bool:
    """`nc [options] [host [port]]`: a port scan (`-z`) sends nothing; a listener (`-l`, `-k`) serves whoever connects; anything else sends what it is fed."""
    hosts, listens, i = [], False, 0
    while i < len(words):
        w = words[i]
        i += 1
        if w.startswith("--") and len(w) > 2:
            if w[2:] in ("listen", "keep-open"):
                listens = True
            continue
        if w.startswith("-") and len(w) > 1:
            for k, c in enumerate(w[1:], 1):
                if c == "z":
                    return False
                if c in "lk":
                    listens = True
                if c in _NC_SHORT_VALUE:
                    if c == "x":    # `-x proxy_address`: the connection goes through the proxy
                        routed = True
                    if not w[k + 1:] and i < len(words):
                        i += 1
                    break
        elif not w.isdigit():
            hosts.append(w)
    if listens:
        return True
    return bool(hosts) and (routed or not all(is_local(h) for h in hosts))


def sends_out(bare: str, proxied: bool = False) -> bool:
    """True when this simple command sends a file, standard input or a variable to a destination that is not this machine. `proxied`: the command
    (not only this segment) assigns a proxy variable (`PROXY_ENV`), so a local-looking destination is no evidence."""
    words = _without_redirects(bare.split())
    if not words:
        return False
    prog = words[0].rsplit("/", 1)[-1]
    routed = proxied
    if prog in _LAUNCHERS:
        routed = routed or prog in _PROXYING_LAUNCHERS
        k = next((n for n, w in enumerate(words[1:], 1) if w.rsplit("/", 1)[-1] in _CLIENTS), None)
        if k is None:
            return False
        words, prog = words[k:], words[k].rsplit("/", 1)[-1]
    if prog == "curl":
        return _curl(words[1:], routed)
    if prog == "wget":
        return _wget(words[1:], routed)
    if prog in ("nc", "ncat", "netcat"):
        return _nc(words[1:], routed)
    return False
