Before running the program, ensure you've installed python: in terminal(windows), input: python --version
;in terminal(mac), input: python3 --version
Then, save the file to the table. In Terminal, open the file by: (windows)cd Desktop (mac)cd ~/Desktop
Then press:(windows) python -m pip install requests beautifulsoup4 rich pyyaml
(mac)python3 -m pip install requests beautifulsoup4 rich pyyaml
Then run the file:(windows)python tsearch.py
(mac)python3 tsearch.py
After you see:搜索 >
You can input the question you want to ask.
See how to use by input ":help".

Or, for a more formalized form, you can use as a reference:
Before running this program, make sure Python is installed.
Check Python version:
- Windows Terminal: `python --version`
- Mac Terminal: `python3 --version`
> Note: If `python` does not work on Windows, try `py --version`

Save the code file as **tsearch.py** and put it on your Desktop.

In Terminal, change directory to Desktop:
- Windows: `cd Desktop`
- Mac: `cd ~/Desktop`

Install required dependencies:
- Windows: `python -m pip install requests beautifulsoup4 rich pyyaml`
- Mac: `python3 -m pip install requests beautifulsoup4 rich pyyaml`

Launch the tool:
- Windows: `python tsearch.py`
- Mac: `python3 tsearch.py`

When you see the prompt `搜索 >`, type anything you want to search.
Type `:help` to view all available commands.

Notes:
- DuckDuckGo / Bing may fail to connect in mainland China network.
- The searxng engine requires a local SearXNG instance running at 127.0.0.1:8888, otherwise it will always fail.
