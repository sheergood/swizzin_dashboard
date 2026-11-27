#!/usr/bin/env python
#Prep the websockets with eventlet workers
import eventlet
eventlet.monkey_patch()

import flask
from core.htpasswd import HtPasswdAuth
from core.middleware import PrefixMiddleware
from flask_socketio import SocketIO, emit
from threading import Thread, Lock
from packaging import version
import os
import core.config
import requests
import time
from werkzeug.middleware.proxy_fix import ProxyFix
import calendar
import hashlib
import re
import secrets
import logging

logging.basicConfig(level=logging.WARN)

async_mode = None

#Prep flask
app = flask.Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

#Config the app
app.config.from_object('core.config.Config')
app.config.from_pyfile('swizzin.cfg', silent=True)
admin_user = app.config['ADMIN_USER']
htpasswd = HtPasswdAuth(app)

#Config the base url
if app.config['URLBASE'] == "/":
    app.config['URLBASE'] = ""

def _signing_secret():
    """The token-signing secret must not be the public default; make one per install."""
    if app.config.get('FLASK_SECRET') not in (None, "", "What's the password?"):
        return app.config['FLASK_SECRET']
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret')
    try:
        with open(path) as f:
            v = f.read().strip()
        if len(v) >= 32:
            return v
    except OSError:
        pass
    v = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(v)
    except OSError:
        pass   # still random for this run; tokens just don't survive a restart
    return v
app.config['FLASK_SECRET'] = _signing_secret()

app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=app.config['URLBASE'])
socketio = SocketIO(app,  path='{}/socket.io'.format(app.config['URLBASE']), async_mode=async_mode)

#Config rate limiting
_auth_cache = {}

def _valid_basic():
    """True when the request carries a valid swizzin login (Basic auth). Cached 5 min per
    login + htpasswd version: htpasswd hashes are deliberately slow and the dashboard polls."""
    a = flask.request.authorization
    if not a or not a.username or not a.password:
        return False
    try:
        stamp = os.stat(app.config['FLASK_HTPASSWD_PATH']).st_mtime
    except OSError:
        stamp = 0
    key = hashlib.sha256("{}\0{}\0{}".format(a.username, a.password, stamp).encode()).hexdigest()
    now = time.time()
    hit = _auth_cache.get(key)
    if hit and now - hit[1] < 300:
        return hit[0]
    ok = bool(htpasswd.users.check_password(a.username, a.password))
    if len(_auth_cache) > 512:
        _auth_cache.clear()
    _auth_cache[key] = (ok, now)
    return ok

#Config rate limiting
def check_authorization():
    # Signed-in users are never rate limited. Only a *valid* login counts: exempting any request
    # that merely had an Authorization header let password guessing run without a limit.
    try:
        return _valid_basic()
    except Exception:
        return False


if app.config['RATELIMIT_ENABLED'] == True:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[app.config['RATELIMIT_DEFAULT']],
        default_limits_exempt_when=check_authorization,
        default_limits_per_method=True
    )

from core.util import *
import core.hardware as hw
import core.place as place
import core.specs as specs
import core.plex as plex
import core.speedtest as speedtest
import core.prefs as prefs

#Prepare the background threads
thread = None
thread_lock = Lock()
thread2 = None
thread2_lock = Lock()


#Background thread functions
def current_speed(app):
    """ Thread for interface speed """
    #Modified for our uses from https://stackoverflow.com/a/26853086
    with app.app_context():
        #print("Starting current speed for", interface)
        interface = get_default_interface()
        (tx_prev, rx_prev, total_prev) = (0, 0, 0)
        while(True):
            tx = get_nic_bytes('tx', interface)
            rx = get_nic_bytes('rx', interface)
            total = tx + rx
            if tx_prev > 0:
                tx_speed = tx - tx_prev
                #print('TX: ', tx_speed, 'bps')
                tx_speed = str(GetHumanReadableBi(tx_speed)) + "/s"
            if rx_prev > 0:
                rx_speed = rx - rx_prev
                #print('RX: ', rx_speed, 'bps')
                rx_speed = str(GetHumanReadableBi(rx_speed)) + "/s"
            if total_prev > 0:
                total_speed = total - total_prev
                #print("TOTAL: ', total_speed, 'bps')
                total_speed = str(GetHumanReadableBi(total_speed)) + "/s"
                emit('speed', {'interface': interface, 'tx': tx_speed, 'rx': rx_speed, 'total': total_speed}, namespace='/websocket', broadcast=True)
            time.sleep(1)
            tx_prev = tx
            rx_prev = rx
            total_prev = total

def io_wait(app):
    """ Thread for iowait emission """
    #https://stackoverflow.com/a/7299268
    tick = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
    numcpu = os.cpu_count()
    interval = 10
    with app.app_context():
        while(True):
            readstats = open('/proc/stat')
            procstats = readstats.readlines()[0].split()
            user, nice, sys, idle, iowait, irq, sirq = ( float(procstats[1]), float(procstats[2]),
                                            float(procstats[3]), float(procstats[4]),
                                            float(procstats[5]), float(procstats[6]),
                                            float(procstats[7]) )
            readstats.close()
            time.sleep(interval)
            readstats = open('/proc/stat')
            procstats = readstats.readlines()[0].split()
            userd, niced, sysd, idled, iowaitd, irqd, sirqd = ( float(procstats[1]), float(procstats[2]),
                                            float(procstats[3]), float(procstats[4]),
                                            float(procstats[5]), float(procstats[6]),
                                            float(procstats[7]) )
            readstats.close()
            iowait = '{0:.1f}'.format(((iowaitd - iowait)* 100 / tick ) / numcpu / interval)
            #times = psutil.cpu_times_percent(interval=10)
            emit('iowait', {'iowait': iowait}, namespace='/websocket', broadcast=True)


@app.before_request
def reload_htpasswd():
    """ 
    This function ensures htpasswd is reloaded and up-to-date before every request.
    """ 
    htpasswd.load_users(app)

#@app.after_request
#def apply_headers(response):
#    if flask.request.referrer == "{}login".format(flask.request.host_url):
#        response.headers["WWW-Authenticate"] = 'basic realm="{0}"'.format(current_app.config['FLASK_AUTH_REALM'])
#    if flask.request.referrer == "{}login/auth".format(flask.request.host_url):
#        response.headers["WWW-Authenticate"] = 'basic realm="{0}"'.format(current_app.config['FLASK_AUTH_REALM'])
#    if flask.request.referrer == "{}logout".format(flask.request.host_url):
#        response.headers["WWW-Authenticate"] = 'basic realm="{0}"'.format(current_app.config['FLASK_AUTH_REALM'])
#    return response

@app.errorhandler(401)
def unauthorized(e):
    if app.config['FORMS_LOGIN']:
        if app.config['URLBASE'] == "":
            urlbase = app.config['URLBASE']
        elif app.config['URLBASE'].startswith("/"):
            urlbase = app.config['URLBASE'][1:]
        if not urlbase == "" and not urlbase.endswith("/"):
            urlbase = urlbase + "/"
        if flask.request.referrer == "{host}{urlbase}login".format(host=flask.request.host_url, urlbase=urlbase):
            return authenticate()
        elif flask.request.referrer == "{host}{urlbase}login/auth".format(host=flask.request.host_url, urlbase=urlbase):
            return authenticate()
        elif flask.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return "UNAUTHORIZED", 401
        else:
            return flask.redirect(flask.url_for('login'))
    else:
        return authenticate()

def authenticate():
    """
    Sends a 401 response that enables basic auth
    """
    return flask.Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials',
        401,
        {'WWW-Authenticate': 'basic realm="{0}"'.format(
            current_app.config['FLASK_AUTH_REALM']
        )}
    )

@app.after_request
def _security_headers(r):
    h = r.headers
    h.setdefault('X-Frame-Options', 'SAMEORIGIN')        # no clickjacking of the service buttons
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('Referrer-Policy', 'same-origin')
    if flask.request.path.startswith(('/api/', '/apps/', '/stats/')) and r.mimetype == 'application/json':
        h.setdefault('Cache-Control', 'no-store')
    return r

def _same_site_xhr():
    """State-changing requests must come from our own pages. The browser attaches the saved Basic
    login to any request, so without this another website could press our buttons; it can't add
    X-Requested-With without a CORS preflight, which we never answer."""
    if flask.request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return False
    origin = flask.request.headers.get('Origin')
    return not origin or origin.split('://', 1)[-1] == flask.request.host

#Begin routes
@app.route('/')
@htpasswd.required
def index(user):
    #global thread
    #if thread is None:
    #    thread = Thread(target=current_speed)
    #    thread.start()
    pages = generate_page_list(user)
    mounts = get_mounts()
    if os.path.isfile("/install/.quota.lock"):
        quota = True
    else:
        quota = False
    if os.path.isfile("/install/.sbio.lock"):
        vendor = "sbio"
    else:
        vendor = "swizzin"
    return flask.render_template('index.html', title='{user} - swizzin'.format(user=user), user=user, pages=pages, quota=quota, vendor=vendor, mounts=mounts, admin=_is_admin(user), prefs=prefs.get(app.config, user))

@app.route('/classic')
@htpasswd.required
def classic(user):
    pages = generate_page_list(user)
    mounts = get_mounts()
    quota = os.path.isfile("/install/.quota.lock")
    vendor = "sbio" if os.path.isfile("/install/.sbio.lock") else "swizzin"
    return flask.render_template('classic.html', title='{user} - swizzin dashboard'.format(user=user), user=user, pages=pages, quota=quota, vendor=vendor, mounts=mounts, async_mode=socketio.async_mode)

def _is_admin(user):
    return user == admin_user or not app.config['SHAREDSERVER']

import functools

def _api(fn):
    """Admin-only JSON endpoint that never takes the panel down: errors come back as JSON."""
    @functools.wraps(fn)
    def wrapper(user, *a, **k):
        if not _is_admin(user):
            return flask.jsonify({"error": "forbidden"}), 403
        try:
            out = fn(user, *a, **k)
            if isinstance(out, tuple):                 # (body, status)
                return flask.jsonify(out[0]), out[1]
            return flask.jsonify(out)
        except Exception as e:
            app.logger.exception("panel api %s failed", flask.request.path)
            return flask.jsonify({"error": "{}: {}".format(type(e).__name__, e)}), 500
    return wrapper

@app.route('/api/system')
@htpasswd.required
@_api
def api_system(user):
    return hw.system()

@app.route('/api/live')
@htpasswd.required
@_api
def api_live(user):
    return hw.live()

@app.route('/api/storage')
@htpasswd.required
@_api
def api_storage(user):
    return {"mounts": hw.mounts_usage(get_mounts()), "drives": hw.drives(), "raid": hw.raid()}

@app.route('/api/place')
@htpasswd.required
@_api
def api_place(user):
    return place.place(app.config, hw._default_iface())

@app.route('/api/weather')
@htpasswd.required
@_api
def api_weather(user):
    return {"weather": place.device_weather(app.config, flask.request.args["lat"], flask.request.args["lon"])}

@app.route('/api/fail2ban')
@htpasswd.required
@_api
def api_fail2ban(user):
    return hw.fail2ban()

@app.route('/api/procs')
@htpasswd.required
@_api
def api_procs(user):
    return hw.processes()

@app.route('/api/traffic')
@htpasswd.required
@_api
def api_traffic(user):
    return hw.traffic()

@app.route('/api/specs')
@htpasswd.required
@_api
def api_specs(user):
    return specs.specs(app.config)

@app.route('/api/plex')
@htpasswd.required
@_api
def api_plex(user):
    return plex.now_playing(app.config)

@app.route('/api/speedtest', methods=['GET', 'POST'])
@htpasswd.required
@_api
def api_speedtest(user):
    if flask.request.method == 'POST':
        # The browser sends cached Basic credentials with any request, so a plain cross-site form
        # could start a 2 GB transfer. Require the header only our own XHR sets (a cross-site page
        # can't add it without a CORS preflight, which we never answer) and a matching Origin.
        if not _same_site_xhr():
            return {"error": "forbidden"}, 403
        ok, msg = speedtest.start(app.config)
        st = speedtest.status(app.config)
        st["started_now"], st["message"] = ok, msg
        return st
    return speedtest.status(app.config)

@app.route('/api/prefs', methods=['GET', 'POST'])
@htpasswd.required
def api_prefs(user):
    if flask.request.method == 'POST':
        if not _same_site_xhr():
            return flask.jsonify({"error": "forbidden"}), 403
        data = flask.request.get_json(silent=True)
        try:
            return flask.jsonify(prefs.update(app.config, user, data))
        except OSError as e:
            app.logger.warning("couldn't save preferences: %s", e)
            return flask.jsonify({"error": "couldn't save preferences"}), 500
    return flask.jsonify(prefs.get(app.config, user))

@socketio.on('connect', namespace='/websocket')
def socket_connect():
    global thread
    global thread2
    if not _valid_basic():
        return False          # live stats are for signed-in users only
    with thread_lock:
        if thread is None:
            thread = socketio.start_background_task(current_speed, (flask.current_app._get_current_object()))
    with thread2_lock:
        if thread2 is None:
            thread2 = socketio.start_background_task(io_wait, (flask.current_app._get_current_object()))
    emit('my_response', {'data': 'Connected', 'count': 0})

@app.route('/stats')
@app.route('/stats/')
@htpasswd.required
def stats(user):
    pages = generate_page_list(user)
    return flask.render_template('stats.html', title='Stats', user=user, pages=pages)

@app.route('/stats/netdata/')
@app.route('/stats/netdata/<path:p>',methods=['GET'])
@htpasswd.required
def netdataproxy(user, p = ''):
    if not _is_admin(user):
        return flask.Response("Forbidden", 403)     # netdata shows every process and user on the box
    SITE = 'http://127.0.0.1:19999/{}'.format(p)
    if flask.request.method=='GET':
        if flask.request.args:
            querystring = flask.request.query_string.decode('utf-8')
            resp = requests.get("{SITE}?{querystring}".format(SITE=SITE,querystring=querystring), timeout=30)
        else:
            resp = requests.get("{SITE}".format(SITE=SITE), timeout=30)
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in  resp.raw.headers.items() if name.lower() not in excluded_headers]
        response = flask.Response(resp.content, resp.status_code, headers)
        return response
    #elif flask.request.method=='POST':
    #    resp = requests.post(f'{SITE}',json=request.get_json())
    #    excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
    #    headers = [(name, value) for (name, value) in resp.raw.headers.items() if name.lower() not in excluded_headers]
    #    response = flask.Response(resp.content, resp.status_code, headers)
    #    return response
    #elif flask.request.method=='DELETE':
    #    resp = requests.delete(f'{SITE}').content
    #    response = flask.Response(resp.content, resp.status_code, headers)
    #    return response


@app.route('/apps/status')
@htpasswd.required
def app_status(user):
    apps = apps_status(user)
    return flask.jsonify(apps)

@app.route('/apps/service', methods=['POST'])
@htpasswd.required
def service(user):
    if not _same_site_xhr():
        return flask.Response("Access denied", 403)
    data = flask.request.get_json(silent=True) or {}
    application, function = data.get('application'), data.get('function')
    if function not in SERVICE_ACTIONS or not isinstance(application, str) or not re.match(r'^[a-z0-9_]{1,40}$', application):
        return flask.Response("Bad request", 400)
    try:
        profile = str_to_class(application+"_meta")(user)
    except Exception:
        try:
            profile = str_to_class(application+"_meta")
        except Exception:
            return """Application profile not found."""
    multiuser = getattr(profile, 'multiuser', False)
    if multiuser == False and user != admin_user:
        return """Access denied"""
    unit = getattr(profile, 'systemd', application)
    if not isinstance(unit, str):
        unit = application
    if unit.endswith("@"):
        unit = unit+user
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9@._-]{0,100}$', unit):
        return flask.Response("Bad request", 400)
    result = systemctl(function, unit)
    return str(result)

SERVICE_ACTIONS = ("start", "stop", "restart", "enable", "disable")


@app.route('/stats/loadavg')
@htpasswd.required
def loadavg(user):
    loadavg = open("/proc/loadavg").readline().split(" ")[:3]
    numcpu = os.cpu_count()
    perutil = '{0:.2f}'.format((float(loadavg[0]) / numcpu) * 100)
    return flask.jsonify({"1m": loadavg[0], "5m": loadavg[1], "15m": loadavg[2], "perutil": perutil})

@app.route('/stats/vnstat')
@htpasswd.required
def vnstat(user):
    #stats = []
    interface = get_default_interface()
    vnstat_info = vnstat_data(interface, "h")
    vnstat_jsonversion = vnstat_info["jsonversion"]
    if vnstat_jsonversion == "1":
        qh = "hours"
        qd = "days"
        qm = "months"
        qt = "tops"
        thisday = 0
        thismonth = 0
        hour = int(time.strftime("%H"))
        lasthour = hour - 1
        if lasthour == -1:
            lasthour = 23
        read_unit = GetHumanReadableKB
    elif vnstat_jsonversion == "2":
        qh = "hour"
        qd = "day"
        qm = "month"
        qt = "top"
        thisday = vnstat_data(interface, "d")['interfaces'][0]['traffic']['day'][-1]["id"]
        thismonth = vnstat_data(interface, "m")['interfaces'][0]['traffic']['month'][-1]["id"]
        hour = vnstat_info['interfaces'][0]['traffic']['hour'][-1]["id"]
        lasthour = vnstat_info['interfaces'][0]['traffic']['hour'][-2]["id"]
        read_unit = GetHumanReadableB
    statsh = vnstat_parse(interface, "h", qh, read_unit, hour)
    statslh = vnstat_parse(interface, "h", qh, read_unit, lasthour)
    statsd = vnstat_parse(interface, "d", qd, read_unit, thisday)
    statsm = vnstat_parse(interface, "m", qm, read_unit, thismonth)
    statsa = vnstat_parse(interface, "h", "total", read_unit)
    #statsa = vnstat_parse(interface, "m", "total", 0)
    days = vnstat_data(interface, "d")['interfaces'][0]['traffic'][qd][::-1]
    timeline = []
    for t in days[:10]:
        date = t['date']
        year = date['year']
        month = calendar.month_abbr[date['month']]
        day = date['day']
        date = "{month} {day}, {year}".format(year=year, month=month, day=day)
        rx = read_unit(t['rx'])
        tx =read_unit(t['tx'])
        total = read_unit(t['tx'] + t['rx'])
        timeline.append({"date": date, "rx": rx, "tx": tx, "total": total})

    if app.config['NETWORK_INFO_TOP']:
        if not app.config['NETWORK_INFO_CUTOFF'] or app.config['NETWORK_INFO_CUTOFF'] == "YYYY-MM-DD":
            tops = vnstat_data(interface, "t")['interfaces'][0]['traffic'][qt]
        else:
            tops = vnstat_data(interface, "t", app.config['NETWORK_INFO_CUTOFF'])['interfaces'][0]['traffic'][qt]
        top = []
        for t in tops[:app.config['NETWORK_INFO_TOPSTATS']]:
            date = t['date']
            year = date['year']
            month = calendar.month_abbr[date['month']]
            day = date['day']
            date = "{month} {day}, {year}".format(year=year, month=month, day=day)
            rx = read_unit(t['rx'])
            tx =read_unit(t['tx'])
            total = read_unit(t['tx'] + t['rx'])
            top.append({"date": date, "rx": rx, "tx": tx, "total": total})

    columns = {"date", "rx", "tx", "total"}
    #stats = []
    #stats.extend({"statsh": statsh, "statslh": statslh, "statsd": statsd, "statsm": statsm, "statsa": statsa, "top": top})
    #print(stats)
    #return flask.jsonify({"statsh": statsh, "statslh": statslh, "statsd": statsd, "statsm": statsm, "statsa": statsa, "top": top})
    return flask.render_template('top.html', user=user, timeline=timeline, day=statsd, month=statsm, hour=statsh, lasthour=statslh, alltime=statsa, colnames=columns, top=top if app.config['NETWORK_INFO_TOP'] else None)

@app.route('/stats/disk')
@htpasswd.required
def disk_free(user):
    mounts = get_mounts()
    data = {}
    for mount in mounts:
        total, used, free, usage = disk_usage(mount)
        data[mount] = {"disktotal": total, "diskused": used, "diskfree": free, "perutil": usage}
    return flask.jsonify(data)

@app.route('/stats/quota')
@htpasswd.required
def quota_free(user):
    if os.path.isfile("/install/.quota.lock"):
        total, used, free, usage = quota_usage(user)
        return flask.jsonify({"quota": {"disktotal": total, "diskused": used, "diskfree": free, "perutil": usage}})
    else:
        return """Quota not installed"""

@app.route('/stats/boot')
@htpasswd.required
def boot_time(user):
    return boottimeutc

@app.route('/stats/ram')
@htpasswd.required
def ram_stats(user):
    ramstats = dict((i.split()[0].rstrip(':'),int(i.split()[1])) for i in open('/proc/meminfo').readlines())
    ramtotal = GetHumanReadableKiB(ramstats['MemTotal'])
    ramfree = GetHumanReadableKiB(ramstats['MemAvailable'])
    ramused = GetHumanReadableKiB(ramstats['MemTotal'] - ramstats['MemAvailable'])
    perutil = '{0:.2f}'.format((ramstats['MemTotal'] - ramstats['MemAvailable']) / ramstats['MemTotal'] * 100)
    return flask.jsonify({"ramtotal": ramtotal, "ramfree": ramfree, "ramused": ramused, "perutil": perutil})

@app.route('/stats/network')
@htpasswd.required
def network_quota(user):
    if app.config['SHAREDSERVER'] is True:
        total, used, free, usage = network_quota_usage(user)
    return flask.jsonify({"nettotal": total, "netused": used, "netfree": free, "perutil": usage})


@app.route('/login')
def login():
    return flask.render_template('login.html', title='swizzin login')

@app.route('/login/auth')
@htpasswd.required
def auth(user):
    return """
        <div>You have been logged in. Redirecting to home...</div>    

<script>
    setTimeout(function () {{
        window.location.href = "{}";
    }}, 500);
</script>
    """.format(flask.url_for('index'))


@app.route('/logout')
@htpasswd.required
def logout(user):
    return flask.render_template('logout.html')

if __name__ == '__main__':
    socketio.run(app, host=app.config['HOST'], port=app.config['PORT'])

    #app.run(debug=True,host='0.0.0.0', port=8333)
