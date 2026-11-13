# Converts Puzzle.round (FK) into Puzzle.rounds (M2M) without losing existing assignments:
#   1. add the M2M field, 2. copy each puzzle's round_id into it, 3. drop the FK.

from django.db import migrations, models


def copy_round_fk_to_m2m(apps, schema_editor):
    Puzzle = apps.get_model('puzzles', 'Puzzle')
    Through = Puzzle.rounds.through
    Through.objects.bulk_create(
        [Through(puzzle_id=pid, round_id=rid)
         for pid, rid in Puzzle.objects.filter(round__isnull=False).values_list('id', 'round_id')],
        batch_size=500,
        ignore_conflicts=True,
    )


def copy_m2m_back_to_fk(apps, schema_editor):
    # Lossy by nature: a puzzle in several rounds keeps only the lowest-id one.
    Puzzle = apps.get_model('puzzles', 'Puzzle')
    Through = Puzzle.rounds.through
    first_round = {}
    for pid, rid in Through.objects.order_by('round_id').values_list('puzzle_id', 'round_id'):
        first_round.setdefault(pid, rid)
    puzzles = list(Puzzle.objects.filter(id__in=first_round))
    for p in puzzles:
        p.round_id = first_round[p.id]
    Puzzle.objects.bulk_update(puzzles, ['round'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('puzzles', '0004_hunt'),
    ]

    operations = [
        migrations.AddField(
            model_name='puzzle',
            name='rounds',
            field=models.ManyToManyField(blank=True, help_text='The round(s) a given puzzle belongs to. A puzzle (e.g. a cross-round meta) can be in several.', related_name='puzzles', to='puzzles.round'),
        ),
        migrations.RunPython(copy_round_fk_to_m2m, copy_m2m_back_to_fk),
        migrations.RemoveField(
            model_name='puzzle',
            name='round',
        ),
    ]
