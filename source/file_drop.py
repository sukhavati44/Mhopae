"""Local Windows shell file drops; no extra packages or external services."""
import ctypes
from ctypes import wintypes
from collections import deque
import sys


class FileDrop:
    def __init__(self, root, callback):
        self.root = root
        self.callback = callback
        self.pending = deque()
        self.closed = False
        self.dispatching = False
        self.timer = None
        if sys.platform != 'win32':
            self.hwnd = None
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        result = ctypes.c_ssize_t
        self.proc_type = ctypes.WINFUNCTYPE(result, wintypes.HWND, wintypes.UINT,
                                            wintypes.WPARAM, wintypes.LPARAM)
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.set_proc = self.user.SetWindowLongPtrW
        self.set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        self.set_proc.restype = ctypes.c_void_p
        self.user.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                             wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallWindowProcW.restype = result
        self.shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                             wintypes.LPWSTR, wintypes.UINT]
        self.shell.DragQueryFileW.restype = wintypes.UINT
        self.shell.DragFinish.argtypes = [wintypes.HANDLE]
        root.update_idletasks()
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        self.proc = self.proc_type(self._window_proc)
        ctypes.set_last_error(0)
        self.original = self.set_proc(self.hwnd, -4, ctypes.cast(self.proc, ctypes.c_void_p))
        if not self.original:
            raise ctypes.WinError(ctypes.get_last_error())
        self.shell.DragAcceptFiles(self.hwnd, True)
        root.bind('<Destroy>', self._destroy, add='+')
        self.timer = root.after(100, self._drain)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == 0x233:  # WM_DROPFILES
            try:
                count = self.shell.DragQueryFileW(wparam, 0xffffffff, None, 0)
                paths = []
                for i in range(count):
                    length = self.shell.DragQueryFileW(wparam, i, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    self.shell.DragQueryFileW(wparam, i, buffer, length + 1)
                    paths.append(buffer.value)
                # Never show modal Tk dialogs inside a native window callback.
                if not self.dispatching:
                    self.pending.append(paths)
            finally:
                self.shell.DragFinish(wparam)
            return 0
        return self.user.CallWindowProcW(self.original, hwnd, message, wparam, lparam)

    def _drain(self):
        self.timer = None
        try:
            if self.pending and not self.closed:
                self.dispatching = True
                paths = self.pending.popleft()
                self.pending.clear()
                self.callback(paths)
        finally:
            self.dispatching = False
            if not self.closed:
                self.timer = self.root.after(100, self._drain)

    def _destroy(self, event):
        if event.widget is self.root:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer:
            self.root.after_cancel(self.timer)
        self.pending.clear()
        if self.hwnd:
            self.shell.DragAcceptFiles(self.hwnd, False)
            self.set_proc(self.hwnd, -4, self.original)
