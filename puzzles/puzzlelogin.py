import mechanize


username = open('configuration/site-username', 'r').read().rstrip()
password = open('configuration/site-password', 'r').read().rstrip()

_br = None

def get_logged_in_browser(login_config):
    br = mechanize.Browser()
    br.set_handle_robots(False)
    br.addheaders = [('User-agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11')]



    r = br.open(login_config["url"])

    if login_config["username"] is not "" and login_config["password"] is not "":
        br.select_form(nr=0)
        br['username'] = login_config["username"]
        br['password'] = login_config["password"]
        br.submit()

    return br

# Use this in scripts that run for a short time
# but fetch a lot of pages.
def fetch_with_single_login(scrape_config):
    global _br

    if _br is None:
        _br = get_logged_in_browser(scrape_config["login"])

    r = _br.open(scrape_config["all_puzzles_url"])
    if r.code != 200:
        print(r.read())
        sys.exit(1)

    return r.read()
