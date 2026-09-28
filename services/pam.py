"""Password check through libpam (ctypes): no python-pam dependency.

Runs as the user; pam_unix checks the password via the setuid unix_chkpwd helper.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import getpass

_pam = ctypes.CDLL(ctypes.util.find_library("pam"))
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.calloc.restype = ctypes.c_void_p
_libc.strdup.restype = ctypes.c_void_p
_libc.strdup.argtypes = [ctypes.c_char_p]

PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2


class _Message(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Response(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_Message)), ctypes.POINTER(ctypes.POINTER(_Response)), ctypes.c_void_p)


class _Conv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


_pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_Conv), ctypes.POINTER(ctypes.c_void_p)]
_pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pam.pam_setcred.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]


def authenticate(password: str, user: str | None = None, service: str = "login") -> bool:
    """Blocking (pam_faildelay adds ~2s on failure): call it off the GTK thread."""
    secret = password.encode()

    @_CONV
    def conv(count, messages, responses, _data):
        # PAM frees the array and every resp string, so both must come from malloc
        array = _libc.calloc(count, ctypes.sizeof(_Response))
        if not array:
            return 5  # PAM_BUF_ERR
        responses[0] = ctypes.cast(array, ctypes.POINTER(_Response))
        for i in range(count):
            if messages[i].contents.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                responses[0][i].resp = _libc.strdup(secret)
        return 0

    handle = ctypes.c_void_p()
    # service "login", like i3lock: auth goes through system-local-login -> system-auth
    if _pam.pam_start(service.encode(), (user or getpass.getuser()).encode(), ctypes.byref(_Conv(conv, None)), ctypes.byref(handle)) != 0:
        return False
    status = _pam.pam_authenticate(handle, 0)
    if status == 0:
        _pam.pam_setcred(handle, 0x0008)  # PAM_REFRESH_CRED, like i3lock
    _pam.pam_end(handle, status)
    return status == 0
