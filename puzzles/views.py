
import os
import os.path
import json
import re
import hmac
import base64
import requests

from ago import human

from datetime import timedelta

from collections import defaultdict

from django.http import  JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.urls import reverse
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.db.transaction import  non_atomic_requests
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.timezone import now

from puzzles.models import AccessLog, Config, Hunt, JitsiRooms, Location, Priority, Puzzle, PuzzleWrongAnswer, QueuedAnswer, \
    Round, Status, SubmittedAnswer, Tag, UploadedFile, UserProfile, QueuedHint
from puzzles.googlespreadsheet import get_last_revs
from puzzles.queries import linked_out_puzzle_ids, log_summary_by_puzzle, recent_solver_counts, recent_solvers_by_puzzle

from puzzles.submit import submit_answer
from puzzles.zulip import zulip_send, zulip_user_account_active, get_user_zulip_id
from puzzles.jaas_jwt import JaaSJwtBuilder
from django.contrib.auth.models import User
from django.conf import settings

jaas_api_key = open('configuration/jaas_api_key').read()
jaas_app_id = open('configuration/jaas_app_id').read()
jaas_private_key = open('configuration/id_rsa_jaas').read()
jaas_webhook_secret = open('configuration/jaas_webhook_secret').read().strip()


# This function is called as part of the auth flow (defined in settings.py), and updates the URL
# for the user's Google profile photo
def update_user_social_data(strategy, *args, **kwargs):
  response = kwargs['response']
  backend = kwargs['backend']
  user = kwargs['user']
  if response['picture']:
    url = response['picture']
    user_profile, created = UserProfile.objects.get_or_create(user=user)
    user_profile.picture = url
    user_profile.save()

# This is a helper function that retrieves the message of the day
def get_motd():
    try:
        return Config.objects.get().motd
    except (Config.DoesNotExist, Config.MultipleObjectsReturned):
        return "Oops, someone broke this message. Please ask an admin to fix it."

# This is a helper function that retrieves Jitsi data
def get_jitsi_data():
    try:
        table = JitsiRooms.objects.all()
        by_user_dict = defaultdict(set)
        for row in table:
            by_user_dict[row.user].add(row.puzzle)

        user_list = sorted(by_user_dict.items())
    except:
        user_list = None
    return user_list

# This provides base context for template views
def base_context(d = {}):
    d1 = dict(d)
    d1['teamname'] = settings.TEAMNAME
    d1['hqphone'] = settings.HQPHONE
    d1['hqemail'] = settings.HQEMAIL
    d1['locations'] = Location.objects.all()
    d1['zulip_url'] = settings.ZULIP_SERVER_URL
    d1['admin_link']=settings.SHOW_ADMIN_LINK
    d1['hunts'] = Hunt.objects.all()
    d1['current_hunt'] = Hunt.current()
    return d1

# An API endpoint for updating user location
@login_required
def api_user_location(request):
    if request.method == "POST":
        location = Location.objects.get(name=request.POST['location'])
        request.user.userprofile.location = location
        request.user.userprofile.save()

        # Re-retrieve the location from the database
        updated_location = request.user.userprofile.location
        return JsonResponse({"location": updated_location.name})

    return JsonResponse({"error": "Invalid request method"}, status=400)

# An API endpoint for updating user location
@login_required
def api_user_hunt(request):
    if request.method == "POST":
        new_value = request.POST['hunt']
        if new_value:
            hunt = Hunt.objects.get(name=new_value)
        else:
            hunt = None
        request.user.userprofile.active_hunt = hunt
        request.user.userprofile.save()

        # Re-retrieve the location from the database
        updated_hunt = request.user.userprofile.active_hunt
        if updated_hunt:
            return JsonResponse({"hunt": updated_hunt.name})
        else:
            return JsonResponse({"hunt": "--current--"})

    return JsonResponse({"error": "Invalid request method"}, status=400)

# API endpoint for uploading multiple files for a puzzle
@login_required
def api_upload_files(request, puzzle_id):
    if request.method != "POST":
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        puzzle = Puzzle.objects.get(id=puzzle_id)
        files = request.FILES.getlist('files')
        names = request.POST.getlist('names')
    
        if not files:
            return JsonResponse({"error": "No files provided"}, status=400)

        for file, name in zip(files, names):

            upload = UploadedFile.objects.create(
                puzzle=puzzle,
                name=name
            )

            upload_dir = os.path.join('/var/www/uploads', str(puzzle.id), str(upload.id))
            os.makedirs(upload_dir, exist_ok=True)

            file_path = os.path.join(upload_dir, file.name)
            with open(file_path, 'wb+') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            
            upload.url = f"{settings.BASE_URL}/uploads/{puzzle.id}/{upload.id}/{file.name}"
            upload.save()
        
        puzzle_files = [{"filename": file.name, "url": str(file.url)} for file in puzzle.uploadedfile_set.order_by('id')]

        return JsonResponse({
            "files": puzzle_files
        })

    except Puzzle.DoesNotExist:
        return JsonResponse({'error': 'Puzzle not found'}, status=404)
    except Exception as e:
        print(e)
        return JsonResponse({'error': str(e)}, status=500)            

# API endpoint for getting updates to the page-top message
@login_required
def api_motd(request):
    try:
        return JsonResponse({"motd": Config.objects.get().motd})
    except (Config.DoesNotExist, Config.MultipleObjectsReturned):
        return JsonResponse({"motd": ""})

# API endpoint for getting puzzle's solver history
@login_required
def api_puzzle_history(request, puzzle_id):
    # One query for all prior viewers (user name + location joined in), one for current viewers.
    prior_logs = (AccessLog.objects.filter(puzzle_id=puzzle_id, accumulatedMinutes__gt=0)
                  .values_list("user_id", "user__first_name", "user__last_name",
                               "user__userprofile__location__name", "accumulatedMinutes"))
    prior_viewers = [{
        "id": user_id,
        "first_name": first_name,
        "last_name": last_name,
        "location": location,
        "hours": round(minutes / 60., 2)
        } for user_id, first_name, last_name, location, minutes in prior_logs]
    current_viewers = (User.objects
                       .filter(accesslog__puzzle_id=puzzle_id,
                               accesslog__sessionEnd__gte=now() - timedelta(seconds=360))
                       .distinct()
                       .select_related('userprofile__location')
                       .order_by('first_name', 'last_name'))
    current_viewers = [{
        "id": item.id,
        "first_name": item.first_name,
        "last_name": item.last_name,
        "location": item.userprofile.location.name,
    } for item in current_viewers]
    return JsonResponse({
        "current_viewers": current_viewers,
        "prior_viewers": prior_viewers,
    }) 

# Picks the hunt whose rounds the overview shows: ?hunt=<id> if given, else the current hunt
# (None if there are no hunts, in which case every round is shown, as before hunts existed).
def hunt_for_request(request):
    hunt_id = request.GET.get('hunt')
    if hunt_id:
        return Hunt.objects.get(id=int(hunt_id))
    return Hunt.current()

# Orders rounds depth-first (each parent immediately followed by its children), preserving `order`
# among siblings. Rounds whose parent isn't in the list are treated as roots. Cycle-safe.
def rounds_depth_first(rounds):
    by_id = {r.id: r for r in rounds}
    children = defaultdict(list)
    roots = []
    for r in rounds:
        if r.parent_round_id in by_id:
            children[r.parent_round_id].append(r)
        else:
            roots.append(r)
    ordered, seen = [], set()
    def visit(r):
        if r.id in seen:
            return
        seen.add(r.id)
        ordered.append(r)
        for child in children[r.id]:
            visit(child)
    for r in roots:
        visit(r)
    for r in rounds:  # anything only reachable through a cycle
        visit(r)
    return ordered

# API endpoint for getting full overview data.
# Runs a fixed number of queries regardless of how many rounds/puzzles there are (see
# docs/dev-notes/plan.md section 3, and BatchedViewsTests.test_query_counts_are_constant).
@login_required
def api_overview(request):
    try:
        hunt = hunt_for_request(request)
    except (ValueError, Hunt.DoesNotExist):
        return JsonResponse({"error": "Unknown hunt"}, status=404)

    overview_dict = {}
    overview_dict["default_priority"] = str(Config.objects.select_related('default_priority').get().default_priority)
    overview_dict["hunt"] = {"id": hunt.id, "name": hunt.name} if hunt else None

    # 1 query: rounds of this hunt, plus legacy rounds not assigned to any hunt.
    rounds_qs = Round.objects.select_related('parent_round')
    if hunt is not None:
        rounds_qs = rounds_qs.filter(Q(hunt=hunt) | Q(hunt__isnull=True))
    rounds = rounds_depth_first(list(rounds_qs))
    round_ids = [r.id for r in rounds]

    # 1 query: round <-> puzzle membership, as bare id pairs.
    RoundLink = Puzzle.rounds.through
    links = list(RoundLink.objects.filter(round_id__in=round_ids).values_list('round_id', 'puzzle_id'))

    # 2 queries: puzzles in those rounds or in no round at all, with status/priority joined and tags prefetched.
    puzzles = list(
        Puzzle.objects
        .filter(Q(Exists(RoundLink.objects.filter(puzzle=OuterRef('pk'), round_id__in=round_ids)))
                | ~Q(Exists(RoundLink.objects.filter(puzzle=OuterRef('pk')))))
        .select_related('status', 'priority')
        .prefetch_related('tags')
    )

    # 2 queries: per-puzzle activity, batched.
    solver_counts = recent_solver_counts()
    linked_out = linked_out_puzzle_ids(request.user)

    def quick_puzzle_info(puzzle):
        p_info = {
            "title": puzzle.title,
            "url": puzzle.url,
            "id": puzzle.id,
            "solver_count": solver_counts.get(puzzle.id, 0),
            "unopened": puzzle.id not in linked_out,
            "tags": puzzle.tag_list(),
            "description": puzzle.description,
            "priority": str(puzzle.priority),
        }
        if puzzle.answer:
            p_info["answer"] = puzzle.answer
        else:
            p_info["status"] = str(puzzle.status)
        return (p_info)

    # Build each puzzle's dict once, even if it appears in several rounds.
    info_by_id = {puzzle.id: quick_puzzle_info(puzzle) for puzzle in puzzles}
    position = {puzzle.id: i for i, puzzle in enumerate(puzzles)}  # Puzzle default ordering
    puzzle_ids_by_round = defaultdict(list)
    in_some_round = set()
    for round_id, puzzle_id in links:
        if puzzle_id in info_by_id:
            puzzle_ids_by_round[round_id].append(puzzle_id)
            in_some_round.add(puzzle_id)

    rounds_output = []
    for round in rounds:
        round_dict = {}
        round_dict["round"] = round.name
        round_dict["id"] = round.id
        round_dict["description"] = round.description if round.description else ""
        if round.parent_round:
            round_dict["parent_round"] = round.parent_round.name
            round_dict["parent_round_id"] = round.parent_round_id
        member_ids = sorted(puzzle_ids_by_round[round.id], key=position.__getitem__)
        if member_ids:
            round_dict["puzzles"] = [info_by_id[pid] for pid in member_ids]
        rounds_output.append(round_dict)

    unassigned = [info_by_id[p.id] for p in puzzles if p.id not in in_some_round]
    if unassigned:
        rounds_output.append(
            {
                "round": "Unassigned",
                "id": "unassigned",
                "puzzles": unassigned
            }
        )

    overview_dict["rounds"] = rounds_output
    overview_dict["statuses"] = [str(status) for status in Status.objects.all()]
    overview_dict["priorities"] = [str(priority) for priority in Priority.objects.all()]

    return JsonResponse(overview_dict)

# Helper function for building a full puzzle info dict
def build_puzzle_dict(user, puzzle_id):
    puzzle_dict = {}
    puzzle_dict["tags"] = [str(tag) for tag in Tag.objects.all()]
    puzzle_dict["statuses"] = [str(status) for status in Status.objects.all()]
    puzzle_dict["priorities"] = [str(priority) for priority in Priority.objects.all()]
    
    puzzle = (Puzzle.objects
              .select_related('status', 'priority')
              .prefetch_related('tags', 'rounds',
                                Prefetch('queuedhint_set',
                                         queryset=QueuedHint.objects.select_related('user').order_by('-id'),
                                         to_attr='hints_newest_first'))
              .get(id=puzzle_id))

    p_info = {
        "title": puzzle.title,
        "url": puzzle.url,
        "id": puzzle.id,
        "hints": [{"user": f"{hint.user.first_name} {hint.user.last_name}",
                   "user_id": hint.user.id,
                   "details": hint.details,
                   "response": hint.response,
                   "submitted": hint.submitted_ago(),
                   "urgent": hint.urgent,
                   "resolved": hint.resolved
                  } for hint in puzzle.hints_newest_first],
        "tags": puzzle.tag_list(),
        "description": puzzle.description,
        "priority": str(puzzle.priority),
        'jitsi_room_id': puzzle.jitsi_room_id(),
    }

    solvers = [{"name": (solver.first_name + " " + solver.last_name),
                     "id": solver.id} for solver in puzzle.recent_solvers().order_by('first_name', 'last_name')]

    puzzle_files = [{"filename": file.name, "url": str(file.url)} for file in puzzle.uploadedfile_set.order_by('id')]

    queued_answers = [answer.answer for answer in puzzle.queuedanswer_set.order_by('-id')]

    wrong_answers = [answer.answer for answer in puzzle.puzzlewronganswer_set.order_by('-id')]

    if queued_answers or wrong_answers:
        p_info["prior_answers"] = {}
        if queued_answers:
            p_info["prior_answers"]["queued"] = queued_answers
        if wrong_answers:
            p_info["prior_answers"]["wrong"] = wrong_answers

    if solvers:
        p_info["solvers"] = solvers

    if puzzle_files:
        p_info["uploaded_files"] = puzzle_files

    rounds = puzzle.round_list()
    if rounds:
        p_info["round"] = puzzle.round_names()
        p_info["rounds"] = [{"id": r.id, "name": r.name} for r in rounds]

    if puzzle.answer:
        p_info["answer"] = puzzle.answer
    else:
        p_info["status"] = str(puzzle.status)

    if puzzle.motp:
        p_info["motp"] = puzzle.motp

    puzzle_dict["puzzle"] = p_info

    return(puzzle_dict)

# API endpoint for getting a single puzzle's data in JSON forat
@login_required
def api_puzzle(request, puzzle_id):

    puzzle_dict = build_puzzle_dict(request.user, puzzle_id)

    return JsonResponse(puzzle_dict)

# API endpoint for updating a puzzle's priority, status, description,
# or tags
@login_required
def api_update_puzzle (request, puzzle_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)  # Parse JSON body
            puzzle = Puzzle.objects.get(id=puzzle_id)
            for key in data:
                if key == "priority":                  
                    priority = Priority.objects.get(text=data["priority"])
                    puzzle.priority = priority
                if key == "status":
                    status = Status.objects.get(text=data["status"])
                    puzzle.status = status
                if key == "description":
                    puzzle.description = data["description"]
                if key == "tags":
                    with transaction.atomic():
                        # One query for the tags that already exist. Missing ones are created one at a
                        # time (not bulk_create) so the new-tag post_save signal still fires.
                        tags_by_name = {tag.name: tag for tag in Tag.objects.filter(name__in=data["tags"])}
                        for name in data["tags"]:
                            if name not in tags_by_name:
                                tags_by_name[name] = Tag.objects.create(name=name)
                        puzzle.tags.set([tags_by_name[name] for name in data["tags"]])
                if key == "hint":
                   q = QueuedHint(puzzle=puzzle, user=request.user, details=data["hint"]["text"], urgent=data["hint"]["urgent"], resolved=False)
                   zulip_send("b+status", "puzzles"+puzzle.zulip_stream_suffix(), puzzle.zulip_topic(),
                              "**%s requested a hint:**\n>%s" % (q.user.get_full_name(),q.details))
                   q.save()
                if key == "hint_resolution":
                   q = QueuedHint.objects.get(id=data["hint_resolution"]["id"])
                   q.resolved = True
                   q.response = data["hint_resolution"]["response"]
                   q.save()
                   zulip_send("b+status","puzzles"+puzzle.zulip_stream_suffix(), puzzle.zulip_topic(),
                               "**Hint request resolved!**\n*We asked:*\n>%s\n\n*...and they replied:*\n>%s"%(q.details, q.response))

            puzzle.save()
            return JsonResponse(build_puzzle_dict(request.user, puzzle_id))
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

# API endpoint for logging a user's view
@login_required
def api_log_a_view(request, puzzle_id):
    puzzle = Puzzle.objects.get(id=puzzle_id)
    if puzzle:
        puzzle.log_a_view(user=request.user)
        return HttpResponse(status=204)
    else:
        return HttpResponse(status=418)

# Puzzles eligible for the unloved lists, with everything the lists display preloaded.
# Config is fetched once; the exempt statuses/tags are used as subqueries.
def unloved_candidates():
    config = Config.objects.get()
    return (Puzzle.objects
            .exclude(status__in=config.unloved_exempt_status.all())
            .exclude(tags__in=config.unloved_exempt_tags.all())
            .select_related('status', 'priority')
            .prefetch_related('rounds'))

# API endpoint for unloved puzzles (by status)
@login_required
def api_unloved(request):
    acceptable_puzzles = list(unloved_candidates())
    summary = log_summary_by_puzzle([p.id for p in acceptable_puzzles])
    last_updates = [(summary[p.id]['last_log'], p) for p in acceptable_puzzles]

    last_updates.sort(key=lambda x: x[0].lastUpdate if x[0] else now())
    puzzles = [{"puzzle": {
                    "id": s[1].id,
                    "title": s[1].title,
                    "round": s[1].round_names(),
                    "description": s[1].description,
                    "priority": str(s[1].priority),
                    "priority_css": s[1].priority.css_name,
                    "status": str(s[1].status),
                },
                "human": human(s[0].lastUpdate,1) if s[0] else "untouched",
                "last_update": s[0].lastUpdate.isoformat() if s[0] else None,
                "updating_username": s[0].user.first_name+" "+s[0].user.last_name if s[0] else "",
                "updating_userid": s[0].user.id if s[0] else 0,
                "freshness": freshness_translator(s[0]),
                "total_time": human(timedelta(hours=spent),1,abbreviate=True,past_tense='{}') if (spent:=(summary[s[1].id]['effort']["solver_hours"]))>0 else '',
                }
               for s in last_updates]
    return JsonResponse({"puzzles": puzzles})

# API endpoint for unloved puzzles (by spreadsheet)
@login_required
def api_unloved_by_spreadsheet(request):
    last_updates = get_last_revs(unloved_candidates())

    puzzles = [{"puzzle": {
                    "id": s['puzzle'].id,
                    "title": s['puzzle'].title,
                    "round": s['puzzle'].round_names(),
                    "description": s['puzzle'].description,
                    "priority": str(s['puzzle'].priority),
                    "priority_css": s['puzzle'].priority.css_name,
                    "status": str(s['puzzle'].status),
                },
                "human": human(s['rev'][0],1) if s['rev'] else "untouched",
                "last_update": s['rev'][0].isoformat() if s['rev'] else None,
                "updating_username": "",
                "updating_userid": 0,
                "freshness": freshness_translator(s['rev']),
                "total_time": 'rev %s'%s['rev'][1] if s['rev'] else '',
                }
               for s in last_updates]
    return JsonResponse({"puzzles": puzzles})

# Main overview page
@login_required
@user_passes_test(zulip_user_account_active,login_url='puzzles.views.need_zulip_login')
def overview(request):
    hunt_requested = UserProfile.objects.get(user=request.user).active_hunt_id
    return render(request, "puzzles/overview.html",
                  context=base_context({"hunt":hunt_requested,}))

# Single puzzle page
@login_required
@user_passes_test(zulip_user_account_active,login_url='puzzles.views.need_zulip_login')
def puzzle(request, puzzle_id):
    puzzle = Puzzle.objects.get(id=puzzle_id)
    return render(request, "puzzles/puzzle.html", context = base_context({
                'id': puzzle_id,
                'title': puzzle.title,
                'refresh': 60
                }))

# A redirector to puzzle's external page with view logging
@login_required
def puzzle_linkout(request,puzzle_id):
    puzzle = Puzzle.objects.get(id=puzzle_id) 
    userLog = AccessLog.objects.get_or_create(puzzle=puzzle,user=request.user)[0]
    userLog.linkedOut = True
    userLog.save()
    return redirect(puzzle.url)

# Redirector to the puzzle's Google Sheet
@login_required
def puzzle_spreadsheet(request, puzzle_id):
    spreadsheet = Puzzle.objects.get(id=puzzle_id).spreadsheet
    if spreadsheet:
        return redirect(spreadsheet)
    return HttpResponse("Oops, the spreadsheet isn't ready quite yet. Please wait a moment and then <a href=\"\">refresh</a> this pane.")

# Redirector to the puzzle's Zulip chat
@login_required
def puzzle_chat(request, puzzle_id):
    puzz = Puzzle.objects.get(id=puzzle_id)
    return redirect("%s/#narrow/stream/puzzles%s/topic/%s" % (settings.ZULIP_SERVER_URL,puzz.zulip_stream_suffix(),puzz.zulip_topic()))

# Used for the main answer queue page to manage answer queue manually
def handle_puzzle_answer(puzzle, user, answer, backsolved, phone, request):
    QueuedAnswer.objects.get_or_create(puzzle=puzzle, answer=answer)
    submission = SubmittedAnswer.objects.create(
        puzzle=puzzle, user=user, answer=answer,
        backsolved=backsolved, phone=phone)
    submit_answer(submission, request)
    submission.success = True
    submission.save()
    zulip_send('b+status', puzzle.zulip_stream_suffix(), puzzle.zulip_topic(),
               ':telephone: %s %s called in %s' %
               (user.first_name, user.last_name, answer))

# Used for the main answer queue page to manage answer queue manually
@login_required
def answer_submit_result(request, answer_id, result):
    queued_answer = QueuedAnswer.objects.get(id=answer_id)
    handle_puzzle_answer_result(queued_answer.puzzle, queued_answer.answer, result)
    queued_answer.delete()
    return redirect(reverse('puzzles.views.answer_queue'))

# Used for the main answer queue page to manage answer queue manually
def handle_puzzle_answer_result(puzzle, answer, result):
    if result == 'correct' or result == 'presumed_correct':
        puzzle.answer = answer
    if result == 'correct':
        puzzle.status = Status.objects.get(css_name='solved')
    puzzle.save()
    if result == 'incorrect':
        try:
            PuzzleWrongAnswer.objects.create(puzzle=puzzle, answer=answer)
        except IntegrityError:
            pass

# Manual answer queue page
@login_required
def answer_queue(request):
    qas = list(QueuedAnswer.objects.select_related('puzzle'))
    # One query for all matching successful submissions; latest one wins if there are several
    # (the old per-answer .get() raised MultipleObjectsReturned and showed "No" in that case).
    submitted = {}
    for sa in (SubmittedAnswer.objects
               .filter(success=True, puzzle_id__in={qa.puzzle_id for qa in qas})
               .select_related('user').order_by('timestamp', 'id')):
        submitted[(sa.puzzle_id, sa.answer)] = sa
    for qa in qas:
        sa = submitted.get((qa.puzzle_id, qa.answer))
        qa.success = sa is not None
        if sa is not None:
            qa.user = sa.user
    return render(request, 'puzzles/answer-queue.html', context={
                'queued_answers': qas,
                'refresh': 5
                })

@login_required
def handle_puzzle_hint(request, puzzle, user, details, urgent):
    q=QueuedHint(puzzle=puzzle, user=user, details=details, urgent=urgent, resolved=False)
    q.save()

@login_required
def hint_queue(request):
    hints = list(QueuedHint.objects.select_related('user', 'puzzle__status', 'puzzle__priority'))
    open_hints = [h for h in hints if not h.resolved]
    puzzle_ids = {h.puzzle_id for h in open_hints}
    summary = log_summary_by_puzzle(puzzle_ids)
    solvers = recent_solvers_by_puzzle(puzzle_ids)
    for h in open_hints:
        h.effort = summary[h.puzzle_id]['effort']
        h.recent_solvers = solvers.get(h.puzzle_id, [])
    return render(request, 'puzzles/hint-queue.html', context=base_context({
                'queued_hints': open_hints,
                'resolved_hints': [h for h in hints if h.resolved],
                'refresh': 5
                }))

@login_required
def hint_resolve(request,id):
    q = QueuedHint.objects.get(id=id)
    q.resolved = not q.resolved
    q.save()
    return redirect(reverse('puzzles.views.hint_queue'))

# Auth endpoints

def logout_user(request):
    logout(request)
    return render(request, 'puzzles/logout.html')

def logout_return(request):
    return render(request, 'puzzles/logout_return.html', )

# Main welcome page
@login_required
def welcome(request):
    return redirect(reverse('puzzles.views.overview'))

# General Jitsi page
@login_required
def jitsi_page(request, room_id, start_muted=False):
    token = JaaSJwtBuilder().withDefaults() \
        .withApiKey(jaas_api_key) \
            .withUserName(request.user.first_name+" "+request.user.last_name) \
                .withUserEmail(request.user.email) \
                    .withModerator(False) \
                        .withAppID(jaas_app_id) \
                            .withUserAvatar(request.user.userprofile.picture) \
                                .signWith(jaas_private_key)
    
    return render(request, "puzzles/jitsi_page.html",
                  context = {
                      'jaas_app_id':jaas_app_id,
                      'jitsi_room_id':jaas_app_id+"/"+room_id,
                      'jwt':token.decode(encoding='utf-8'),
                      'start_muted': start_muted,
                  })

# Puzzle-specific Jitsi page
@login_required
def puzzle_jitsi_page(request, puzzle_id):
    start_muted = bool(request.GET.get('start_muted'))
    puzzle = Puzzle.objects.select_related().get(id=puzzle_id)
    return jitsi_page(request,puzzle.jitsi_room_id(), start_muted)

@login_required
def need_zulip_login(request):
  return render(request, "puzzles/zulip_not_logged_in.html",context = base_context({'zulip_url':settings.ZULIP_SERVER_URL}))
  
# Who's on What page
@login_required
def who_what(request):
    all_recent = (AccessLog.objects.filter(lastUpdate__gte=now()-timedelta(seconds=120))
                  .distinct().select_related('user', 'puzzle'))
    jitsi_rooms = JitsiRooms.objects.select_related('user', 'puzzle')
    rdict = defaultdict(lambda: {"puzzles": set(), "rooms": set()})
    for el in all_recent:
        rdict[el.user]["puzzles"].add(el.puzzle)
    for room in jitsi_rooms:
        if room.puzzle:
            rdict[room.user]["rooms"].add(room.puzzle)
    people = list(rdict.items())
#    print(people)
    return render(request, "puzzles/whowhat.html", context = base_context({
        'people':people}))

@login_required
@user_passes_test(zulip_user_account_active,login_url='puzzles.views.need_zulip_login')
def unloved(request):
  return render(request, "puzzles/unloved.html", context = base_context())

@login_required
def unloved_by_spreadsheet(request):
  last_updates = get_last_revs(unloved_candidates())
    

  puzzles = [{"puzzle": s['puzzle'],
              "human":human(s['rev'][0],1) if s['rev'] else "untouched",
              "updating_username": "",
              "updating_userid":0,
              "freshness": freshness_translator(s['rev']),
              "total_time": 'rev %s'%s['rev'][1] if s['rev'] else '',
              }
             for s in last_updates]
  return render(request, "puzzles/unloved.html", context = base_context({"puzzles":puzzles}))


def freshness_translator(log):
  if not log:
    return "untouched"
  try:
    delta = now()-log.lastUpdate
  except AttributeError:
    delta = now()-log[0]
    
  if delta>(10*settings.MAX_GAP_LENGTH):
    return "rancid"
  if delta>(2*settings.MAX_GAP_LENGTH):
    return "questionable"
  return "fresh"

def zulip_dm(request,user_id):
  zulip_id=get_user_zulip_id(User.objects.get(id=user_id))
  return redirect("%s/#narrow/dm/%s" % (settings.ZULIP_SERVER_URL,zulip_id))


# Google profile photo retriever (direct embedding doesn't work because of
# Google's restrictions)
@login_required
def profile_photo(request, id = None):
    svg_placeholder = """
    <svg  xmlns="http://www.w3.org/2000/svg"  width="24"  height="24"  viewBox="0 0 24 24"  fill="none"  stroke="currentColor"  stroke-width="2"  stroke-linecap="round"  stroke-linejoin="round"  class="icon icon-tabler icons-tabler-outline icon-tabler-user-circle"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /><path d="M12 10m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0" /><path d="M6.168 18.849a4 4 0 0 1 3.832 -2.849h4a4 4 0 0 1 3.834 2.855" /></svg>
    """
    if not id:
        user_profile = request.user.userprofile
    else:
        try:
            user_profile = User.objects.get(id=id).userprofile
        except:
            return HttpResponse(
                svg_placeholder,
                content_type="image/svg+xml"
            )
    if user_profile.picture:
        response = requests.get(user_profile.picture, stream=True)
        if response.status_code == 200:
            return HttpResponse(
                response.content,
                content_type=response.headers['Content-Type']
            )


    return HttpResponse(
        svg_placeholder,
        content_type="image/svg+xml"
    )

@csrf_exempt
@require_POST
@non_atomic_requests
def jaas_webhook(request):
    jaas_signature = request.headers["X-Jaas-Signature"]
    sigdict = {k:v for k,v in (element.split('=',maxsplit=1)
                for element in jaas_signature.split(','))}
    payload_to_sign = sigdict["t"]+"."+request.body.decode("utf-8")
    signed = base64.b64encode(hmac.digest(
        jaas_webhook_secret.encode(),
        payload_to_sign.encode(),digest='sha256'))
    if hmac.compare_digest(signed,sigdict["v1"].encode()):
        bdict = json.loads(request.body)
        email = bdict["data"]["email"]
        solver = User.objects.get(email=email)
        room_id = bdict["fqn"].split("/")[1]
        match_obj = re.match(r"^(\w+)-(\d+)-(\w+)$",room_id)
        if match_obj:
            puzzle_id = int(match_obj[2])
            puzzle = Puzzle.objects.get(id=puzzle_id)
            other_id = ''
        else:
            puzzle = None
            other_id = room_id

        event_type = bdict["eventType"]
        if event_type=="PARTICIPANT_JOINED":
            #            webhooklog.write("%s joined %d!\n"%(email,puzzle_id))
            JitsiRooms(user=solver,puzzle=puzzle,string_id=other_id).save()
        elif event_type=="PARTICIPANT_LEFT":
            #            webhooklog.write("%s left %d!\n"%(email,puzzle_id))
            jr = JitsiRooms.objects.filter(user=solver,puzzle=puzzle, string_id=other_id).first()
            jr.delete()
            #        else:
            #            webhooklog.write("unhandled event type %s\n"%bdict["event_type"])
            #    else:
            #        webhooklog.write("verification failed\n")
            #    webhooklog.flush()
        
    return HttpResponse("Message received okay.", content_type="text/plain")
