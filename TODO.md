# TODO List

- [ ] Style play, back, forward buttons
- [ ] Fix random rectangle in bottom right corner
- [ ] Version string in status bar bottom left (accounting for macOS rounded corners)
- [ ] Fix tabs not spanning full width
- [ ] Play one track into another in playlist
- [ ] Figure out what the deal is with clicking back onto a playlist vs. burn list and why they don't change immediately unless you click off and then back on again
- [ ] Linux burn support
- [ ] Windows burn support
- [ ] MP3 file burning plus a prompt if more than the limit tracks are added to a burn list
- [ ] Keyboard shortcuts window
- [ ] Homebrew packaging once public
- [ ] Logging!!!

Handle the following update checker 404 error cleanly:
task failed with an unexpected HTTPError:
Traceback (most recent call last):
  File "/Users/martincurkovic/Development/Dubcrate/src/dubcrate/ui/tasks.py", line 71, in run
    result = self._fn()
  File "/Users/martincurkovic/Development/Dubcrate/src/dubcrate/updates.py", line 88, in check_for_upd
ate
    with urlopen(request, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as response:
         ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 189, in urlopen
    return opener.open(url, data, timeout)
           ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 495, in open
    response = meth(req, response)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 604, in http_response
    response = self.parent.error(
        'http', request, response, code, msg, hdrs)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 533, in error
    return self._call_chain(*args)
           ~~~~~~~~~~~~~~~~^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 466, in_call_chain
    result = func(*args)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 613, in http_error_default
    raise HTTPError(req.full_url, code, msg, hdrs, fp)
urllib.error.HTTPError: HTTP Error 404: Not Found

task failed with an unexpected HTTPError:
Traceback (most recent call last):
  File "/Users/martincurkovic/Development/Dubcrate/src/dubcrate/ui/tasks.py", line 71, in run
    result = self._fn()
  File "/Users/martincurkovic/Development/Dubcrate/src/dubcrate/updates.py", line 88, in check_for_upd
ate
    with urlopen(request, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as response:
         ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 189, in urlopen
    return opener.open(url, data, timeout)
           ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 495, in open
    response = meth(req, response)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 604, in http_response
    response = self.parent.error(
        'http', request, response, code, msg, hdrs)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 533, in error
    return self._call_chain(*args)
           ~~~~~~~~~~~~~~~~^^^^^^^
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 466, in_call_chain
    result = func(*args)
  File "/opt/homebrew/Cellar/python@3.13/3.13.18/Frameworks/Python.framework/Versions/3.13/lib/python3
.13/urllib/request.py", line 613, in http_error_default
    raise HTTPError(req.full_url, code, msg, hdrs, fp)
urllib.error.HTTPError: HTTP Error 404: Not Found
