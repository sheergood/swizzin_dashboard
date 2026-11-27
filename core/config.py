import pwd

class Config:
    try:
        ADMIN_USER = pwd.getpwuid(1000).pw_name
    except:
        ADMIN_USER = None
    APPLICATION_ROOT = '/'
    FLASK_HTPASSWD_PATH = '/etc/htpasswd'
    FLASK_SECRET = "What's the password?"
    FLASK_AUTH_REALM = "What's the password?"
    HOST = "127.0.0.1"   # nginx is the only way in (TLS, real client IPs); set HOST in swizzin.cfg to override
    PORT = "8333"
    RATELIMIT_ENABLED = True
    RATELIMIT_DEFAULT = "5 per minute"
    URLBASE = ""
    SHAREDSERVER = False
    FORMS_LOGIN = True
    NETWORK_TOTALS = False
    DISK_UNITS = "iec"

    NETWORK_INFO_CUTOFF = "YYYY-MM-DD"
    NETWORK_INFO_TOP = True
    NETWORK_INFO_TOPSTATS = 5
    NETWORK_INFO_TOTALS = True