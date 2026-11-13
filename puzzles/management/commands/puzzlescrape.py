import json

from django.core.management.base import BaseCommand
import django.db.utils
from puzzles.models import Hunt, Puzzle, Tag, Status, Round
import re
import json
from bs4 import BeautifulSoup


# from lxml import etree
import urllib.parse
from datetime import datetime

from puzzles import puzzlelogin

base_url = 'https://www.starrats.org'


def get_solved_status():
    # Looked up at run time rather than import time, so merely importing this module needs no DB row.
    return Status.objects.get(text='solved!')


def extract_activity_log_from_file(html_content):
    # Create BeautifulSoup object
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all script tags
    scripts = soup.find_all('script')

    # Look for the script containing window.initialActivityLog
    for script in scripts:
        if script.string and 'window.initialAllPuzzlesState' in script.string:
            # Extract the array content using regex
            #print(script.string)
            match = re.search(r'window\.initialAllPuzzlesState\s*=\s*(.*?)$',
                            script.string, re.DOTALL)
            if match:
                # Get the JSON string and parse it
                json_str = match.group(1)
                #print(json_str)
                try:
                    return json.loads(json_str)
                    #return None
                except json.JSONDecodeError as e:
                    raise ValueError(f"Failed to parse JSON: {e}")



def create_puzzle(title, url, round_obj, is_meta=False, answer=None, desc=None):
    # url = base_url + url
    print(title, url, round_obj, is_meta, answer)
    puzzle = None

    try:
        puzzle = Puzzle.objects.get(url=url)
        print('Already exists')
        if round_obj is not None:
            # Puzzles (e.g. cross-round metas) can be listed under several rounds; .add() is idempotent.
            puzzle.rounds.add(round_obj)
    except Puzzle.DoesNotExist:
        # Look for Submit Answer link
        # puzzle_page = puzzlelogin.fetch_with_single_login(url)
        # doc = etree.HTML(puzzle_page)

        # answer_links = doc.xpath("//div[@id='submit']/a[text()='Check answer']")
        # if len(answer_links) == 1:
        #     answer_link = answer_links[0]
        #     checkAnswerLink = urlparse.urljoin(url, answer_link.get('href'))
        # else:
        #     checkAnswerLink = ''

        print('Adding')

        try:
            if desc:
                puzzle = Puzzle.objects.create(title=title, url=url, checkAnswerLink='', description=desc)
            else:
                puzzle = Puzzle.objects.create(title=title, url=url, checkAnswerLink='')
            if round_obj is not None:
                puzzle.rounds.add(round_obj)
            if is_meta:
                puzzle.tags.add(Tag.objects.get(name='meta'))
            print("Created puzzle (%s, %s)" % (title, url))
        except django.db.utils.IntegrityError:
            # puzzle already exists (race)
            puzzle = Puzzle.objects.filter(url=url).first()

    if answer is not None and puzzle is not None:
        puzzle.answer = answer
        puzzle.status = get_solved_status()
        puzzle.save()
    return puzzle

def html_to_tag(html):
    return html

PUZZLE_URL_PREFIX = "https://www.two-pi-noir.agency/puzzles/"


def sync_state(state):
    """Create/update rounds and puzzles from the hunt's all-puzzles state.

    Batched: existing puzzles, the current hunt's rounds and existing round memberships are each loaded
    with one query up front, so already-known puzzles cost no queries unless something changed."""
    solved_status = get_solved_status()
    # Rounds are scoped to the current hunt so same-named rounds from past hunts aren't reused.
    hunt = Hunt.current()
    rounds_by_name = {r.name: r for r in Round.objects.filter(hunt=hunt)}
    puzzles_by_url = {p.url: p for p in Puzzle.objects.all()}
    RoundLink = Puzzle.rounds.through
    memberships = set(RoundLink.objects.values_list('puzzle_id', 'round_id'))

    for round in state['rounds']:
        round_obj = rounds_by_name.get(round['title'])
        if round_obj is None:
            round_obj = rounds_by_name[round['title']] = Round.objects.create(hunt=hunt, name=round['title'])
        for puzzle in round['puzzles']:
            if puzzle['state'] != 'unlocked':
                continue
            puzzle_answer = puzzle.get('answer', None)
            puzzle_url = PUZZLE_URL_PREFIX + puzzle['slug']
            puzzle_desc = puzzle.get('desc', None)
            puzzle_is_meta = puzzle.get('is_meta', None)
            puzzle_obj = puzzles_by_url.get(puzzle_url)
            if puzzle_obj is None:
                print("[%s](%s) in %s, '%s'" % (puzzle_url, puzzle['title'], round['title'], puzzle_desc))
                try:
                    puzzle_obj = create_puzzle(puzzle['title'], puzzle_url, round_obj=round_obj,
                                               desc=puzzle_desc, is_meta=puzzle_is_meta)
                except Exception as e:
                    print (e)
                    continue
                if puzzle_obj is None:
                    continue
                puzzles_by_url[puzzle_url] = puzzle_obj
                memberships.add((puzzle_obj.id, round_obj.id))
            if (puzzle_obj.id, round_obj.id) not in memberships:
                # Already-known puzzle listed under (another) round: record that membership too.
                puzzle_obj.rounds.add(round_obj)
                memberships.add((puzzle_obj.id, round_obj.id))
            if puzzle_answer and (puzzle_obj.answer != puzzle_answer or puzzle_obj.status_id != solved_status.id):
                puzzle_obj.status = solved_status
                puzzle_obj.answer = puzzle_answer
                puzzle_obj.save()


def print_dry_run(state):
    known_urls = set(Puzzle.objects.values_list('url', flat=True))
    for round in state['rounds']:
        for puzzle in round['puzzles']:
            if puzzle['state'] == 'unlocked':
                puzzle_answer = puzzle.get('answer', None)
                puzzle_url = PUZZLE_URL_PREFIX + puzzle['slug']
                if puzzle_answer:
                    print(puzzle_answer)
                elif puzzle_url not in known_urls:
                    print("[%s](%s) in %s" % (puzzle_url, puzzle['title'], round['title']))


class Command(BaseCommand):
    help = "Visit Hunt Overview and create new puzzles"
    def add_arguments(self, parser):
        parser.add_argument('--file', type=str,
                            help="Read the all-puzzles state JSON (same shape as window.initialAllPuzzlesState) "
                                 "from this file instead of fetching it from the hunt site.")
        parser.add_argument('--dry-run',action='store_true')
    def handle(self, *args, **kwargs):
        all_puzzles_url = "https://www.two-pi-noir.agency/all_puzzles"

        print("Beginning puzzlescrape run at " + datetime.now().isoformat())

        if kwargs['file']:
            with open(kwargs['file']) as f:
                state = json.load(f)
        else:
            response = puzzlelogin.fetch_with_single_login(all_puzzles_url)
            state = extract_activity_log_from_file(response)

        if kwargs['dry_run']:
            print_dry_run(state)
        else:
            sync_state(state)

        print("Finished puzzlescrape run")
