from typing import Any

from django.contrib import admin
from django.db.models import ManyToManyField
from django.forms import ModelMultipleChoiceField
from django.http import HttpRequest
from ordered_model.admin import OrderedModelAdmin
from puzzles.models import Hunt, Round, Status, Priority, Tag, UploadedFile, Location, Puzzle, SubmittedAnswer, \
    PuzzleWrongAnswer, Config, AccessLog, JitsiRooms, PuzzleFolder, PuzzleTemplate, QueuedAnswer, QueuedHint, UserProfile, UserZulipStatus


class SlugAdmin(OrderedModelAdmin):
	list_display = ('text', 'move_up_down_links')
	prepopulated_fields = {'css_name': ('text',)}
admin.site.register(Status, SlugAdmin)
admin.site.register(Priority, SlugAdmin)

class ItemAdmin(OrderedModelAdmin):
	list_display = ('name', 'move_up_down_links')
class RoundInline(admin.TabularInline):
	model = Round
	fields = ('name', 'parent_round', 'description')
	extra = 0
	show_change_link = True

class HuntAdmin(OrderedModelAdmin):
	list_display = ('name', 'is_current', 'move_up_down_links')
	inlines = [RoundInline]
admin.site.register(Hunt, HuntAdmin)

class RoundAdmin(OrderedModelAdmin):
	list_display = ('name', 'hunt', 'parent_round', 'move_up_down_links')

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		active_hunt = UserProfile.objects.get(user=request.user).active_hunt
		print(active_hunt)
		if active_hunt:
			return qs.filter(hunt=active_hunt)
		else:
			return qs

	#list_filter = ('hunt',)
	list_select_related = ('hunt', 'parent_round')
	fields = ('hunt', 'name', 'parent_round', 'description')
admin.site.register(Round, RoundAdmin)
admin.site.register(Tag, ItemAdmin)
admin.site.register(Location, ItemAdmin)
class PuzzleAdmin(OrderedModelAdmin):
	list_display = ('title', 'status', 'priority', 'answer', 'move_up_down_links')
	list_filter = ('status', 'priority', 'rounds__hunt', 'rounds')
	search_fields = ('title', 'answer')
	filter_horizontal = ('rounds',)
	fields = ('title', 'url', 'hunt','status', 'priority', 'tags', 'motp', 'description', 'rounds',
			  'solvers', 'topic_name', 'spreadsheet', 'answer', 'checkAnswerLink', 'template', 'folder')
	def get_queryset(self, request):
		qs = super().get_queryset(request)
		active_hunt = UserProfile.objects.get(user=request.user).active_hunt
		if active_hunt:
			return qs.filter(hunt=active_hunt)
		else:
			return qs
	def formfield_for_manytomany(self, db_field, request, **kwargs):
		if db_field.name == "rounds":
			active_hunt = UserProfile.objects.get(user=request.user).active_hunt
			if active_hunt:
				kwargs["queryset"] = Round.objects.filter(hunt=active_hunt)
			else:
				kwargs["queryset"] = Round.objects.all()
		return super().formfield_for_manytomany(db_field, request, **kwargs)

	def get_readonly_fields(self,request, obj=None):
		if obj:
			return ['template','folder']
		else:
			return []

admin.site.register(Puzzle, PuzzleAdmin)

admin.site.register(PuzzleWrongAnswer)
class SubmittedAnswerAdmin(admin.ModelAdmin):
	list_display = ('puzzle', 'answer', 'user', 'backsolved', 'success', 'timestamp')
	list_filter = ('timestamp', 'backsolved', 'success')
	search_fields = ('answer',)
	ordering = ('-timestamp',)

admin.site.register(SubmittedAnswer, SubmittedAnswerAdmin)

admin.site.register(Config)
class LogAdmin(OrderedModelAdmin):
	list_display = ('user','puzzle','accumulatedMinutes','lastUpdate')
	list_filter = ('user','puzzle')
	readonly_fields = ['lastUpdate']
admin.site.register(AccessLog, LogAdmin)

class JitsiAdmin(OrderedModelAdmin):
	list_display = ('user','puzzle','string_id')
	list_filter = ('user','puzzle','string_id')
admin.site.register(JitsiRooms,JitsiAdmin)
class DriveAdmin(OrderedModelAdmin):
	list_display = ('name','fid')
admin.site.register(PuzzleFolder,DriveAdmin)
admin.site.register(PuzzleTemplate,DriveAdmin)

admin.site.register(UploadedFile,admin.ModelAdmin)
admin.site.register(QueuedAnswer,OrderedModelAdmin)
admin.site.register(QueuedHint,OrderedModelAdmin)

class UserProfileAdmin(admin.ModelAdmin):
	list_display = ('user','active_hunt')
admin.site.register(UserProfile,UserProfileAdmin)


class UserZulipStatusAdmin(admin.ModelAdmin):
	list_display = ('user','status','zulip_id')
admin.site.register(UserZulipStatus,UserZulipStatusAdmin)
