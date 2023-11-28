#!/usr/bin/env python3
import argparse
import collections
import configparser
import dataclasses
import datetime
import itertools
import pathlib
import sys
from typing import Iterable

import requests

assert sys.version_info >= (3, 11), "You need Python 3.11 or newer"  # nosec


Entry = collections.namedtuple('Entry', ('start', 'end', 'message'))
JiraWorkLog = collections.namedtuple('JiraWorkLog', ('id', 'start', 'end'))
TickSyncStatus = collections.namedtuple(
    'TickSyncStatus', ('entry', 'json', 'action'))


@dataclasses.dataclass
class Project:
    name: str
    id: int
    tasks: list | None


Task = collections.namedtuple('Task', ('name', 'id'))


@dataclasses.dataclass
class WorkLog:

    entry: Entry
    text: str
    task: str
    task_id: int

    def __post_init__(self) -> None:
        self.start = self.entry.start
        self.end = self.entry.end
        self.hours = round(
            int((self.entry.end - self.entry.start).total_seconds()) / 3600,
            2)


class ConfigurationError(Exception):
    pass


class CommunicationError(Exception):
    pass


class DataError(Exception):
    pass


def read_config(config_file: pathlib.Path) -> dict:
    if not config_file.exists():
        raise ConfigurationError(
            f"Configuration file {config_file} does not exist.")

    config = configparser.ConfigParser()
    config.optionxform = str  # do not lowercase the aliases section!
    config.read(config_file)

    if not config.has_section('gtimelog2tick'):
        raise ConfigurationError(
            f"Section [gtimelog2tick] is not present in {config_file} config"
            " file.")

    if not config.has_section('gtimelog'):
        raise ConfigurationError(
            f"Section [gtimelog] is not present in {config_file} config file.")

    subscription_id = config['gtimelog2tick'].get('subscription_id')
    token = config['gtimelog2tick'].get('token')
    user_id = config['gtimelog2tick'].get('user_id')
    email = config['gtimelog2tick'].get('email')
    timelog = config['gtimelog2tick'].get('timelog')
    ticklog = config['gtimelog2tick'].get('ticklog')
    requested_projects = config['gtimelog2tick'].get('projects')
    midnight = config['gtimelog'].get('virtual_midnight', '06:00')

    if not subscription_id:
        raise ConfigurationError(
            "The Tick subscription id is not specified, set it via the"
            " gtimelog2tick.subscription_id setting. Take its value from your"
            " profile page on tickspot.com.")
    url = f'https://secure.tickspot.com/{subscription_id}/api/v2'

    if not token:
        raise ConfigurationError(
            "The Tick API token is not specified, set it via the"
            " gtimelog2tick.token setting. Take its value from your profile"
            " page on tickspot.com.")

    if not user_id:
        raise ConfigurationError(
            "The Tick user ID is not specified, set it via the"
            " gtimelog2tick.user_id setting. Take its value from the URL of"
            " your profile page on tickspot.com.")

    if not email:
        raise ConfigurationError(
            "Your email address is not specified, set it via the"
            " gtimelog2tick.email setting.")

    if not requested_projects:
        raise ConfigurationError(
            "The list of projects is not specified, set them via the"
            " gtimelog2tick.projects setting. Use the first letters of the"
            " projects relevant for you from the projects page on"
            " tickspot.com.")

    requested_projects = set(requested_projects.split())

    if not timelog:
        timelog = config_file.parent / 'timelog.txt'

    timelog = pathlib.Path(timelog).expanduser().resolve()
    if not timelog.exists():
        raise ConfigurationError(f"Timelog file {timelog} does not exist.")

    if not ticklog:
        ticklog = config_file.parent / 'ticklog.txt'
    ticklog = pathlib.Path(ticklog).expanduser().resolve()
    try:
        ticklog.open('a').close()
    except OSError as e:
        raise ConfigurationError(
            f"Tick log file {ticklog} is not writable: {e}.")

    session = requests.Session()

    config = {
        'api': url,
        'token': token,
        'user_id': user_id,
        'email': email,
        'timelog': timelog,
        'ticklog': ticklog,
        'requested_projects': requested_projects,
        'session': session,
        'midnight': midnight,
    }

    raw_projects = call(config, 'get', '/projects.json')
    tick_projects = [Project(x['name'], x['id'], None)
                     for x in raw_projects]
    config['tick_projects'] = tick_projects
    return config


def read_timelog(
        f: Iterable[str],
        midnight='06:00',
        tz=None) -> Iterable[Entry]:
    last = None
    nextday = None
    hour, minute = map(int, midnight.split(':'))
    midnight = {'hour': hour, 'minute': minute}
    day = datetime.timedelta(days=1)
    entries = 0
    last_note = None
    for line in f:
        line = line.strip()
        if line == '':
            continue

        try:
            time, note = line.split(': ', 1)
            time = datetime.datetime.strptime(
                time, '%Y-%m-%d %H:%M').astimezone()
        except ValueError:
            continue

        if nextday is None or time >= nextday:
            if last is not None and entries == 0:
                yield Entry(last, last, last_note)
            entries = 0
            last = time
            last_note = note
            nextday = time.replace(**midnight)
            if time >= nextday:
                nextday += day
            continue

        yield Entry(last, time, note)

        entries += 1
        last = time
        last_note = note

    if last is not None and entries == 0:
        yield Entry(last, last, last_note)


def parse_entry_message(
    config: dict,
    message: str
) -> tuple[str, str, int]:
    """Parse entry message into task, text and task_id."""
    project_name, task_name, *text_parts = message.split(':')
    task_name = task_name.strip()

    tick_projects = [
        x
        for x in config['tick_projects']
        if x.name.startswith(project_name)]

    if not tick_projects:
        raise DataError(f'Cannot find a Tick project matching {message}.')
    if len(tick_projects) > 1:
        raise DataError(f'Found multiple Tick projects matching {message}: '
                        f'{", ".join(x.name for x in tick_projects)}.')
    tick_project = tick_projects[0]
    if tick_project.tasks is None:
        raw_tasks = call(
            config, 'get', f'/projects/{tick_project.id}/tasks.json')
        tick_project.tasks = [Task(x['name'], x['id']) for x in raw_tasks]

    possible_tasks = [
        x
        for x in tick_project.tasks
        if x.name.startswith(task_name)]

    if not possible_tasks:
        raise DataError(f'Cannot find a Tick task matching {message}.')
    if len(possible_tasks) > 1:
        raise DataError(f'Found multiple Tick tasks matching {message}: '
                        f'{", ".join(x.name for x in tick_project.tasks)}.')

    task_id = possible_tasks[0].id
    task_name = f'{tick_project.name}: {possible_tasks[0].name}'

    return task_name, ':'.join(text_parts).strip(), task_id


def parse_timelog(
    config: dict,
    entries: Iterable[Entry],
) -> Iterable[WorkLog]:
    for entry in entries:
        # Skip all non-work related entries.
        if entry.message.endswith('**'):
            continue
        # Skip all lines which do not match the requested projects.
        if not any(entry.message.startswith(x)
                   for x in config['requested_projects']):
            continue

        task, text, task_id = parse_entry_message(config, entry.message)
        worklog = WorkLog(entry, text, task, task_id)
        if worklog.hours > 0:
            yield worklog


def get_now():
    return datetime.datetime.now().astimezone()


def filter_timelog(
        entries: Iterable[WorkLog],
        *,
        since=None,
        until=None) -> Iterable[WorkLog]:
    if since is None:
        since = get_now() - datetime.timedelta(days=7)

    for entry in entries:
        if since and entry.start < since:
            continue
        if until and entry.end > until:
            continue
        yield entry


def call(
    config: dict,
    verb: str,
    path: str,
    expected_status_codes: set[int] = {200},
    data: dict | None = None,

) -> dict | None:
    caller = getattr(config['session'], verb)
    headers = {'content-type': 'application/json; charset=utf-8',
               'user-agent': f'gtimelog2tick ({config["email"]})',
               'authorization': f'Token token={config["token"]}'}
    kwargs = {
        'url': f'{config["api"]}{path}',
        'headers': headers}
    if data:
        kwargs['json'] = data
    err = None
    for _ in range(10):
        try:
            response = caller(**kwargs)
        except requests.exceptions.ConnectionError as e:
            err = e
            continue
        else:
            break
    else:
        raise err

    if response.status_code not in expected_status_codes:
        raise CommunicationError(
            f'Error {response.status_code}: {response.text}')
    if verb == 'delete':
        return ''
    return response.json()


def remove_tick_data(
    config: dict,
    date: datetime.date,
    dry_run: bool
) -> Iterable[TickSyncStatus]:
    """Remove pre-existing data in tick."""
    next_day = date + datetime.timedelta(days=1)
    get_path = (
        f'/users/{config["user_id"]}/entries.json'
        f'?start_date={date.isoformat()}'
        f'&end_date={next_day.isoformat()}'
    )
    entries = call(config, 'get', get_path)
    for entry in entries:
        date = datetime.datetime.strptime(entry['date'], '%Y-%m-%d')
        sync_entry = WorkLog(
            Entry(date,
                  date + datetime.timedelta(hours=entry['hours']),
                  entry["id"]),
            entry["notes"], '???', entry["task_id"]
        )
        if dry_run:
            yield TickSyncStatus(sync_entry, {}, 'delete (dry run)')
        else:
            call(config, 'delete', f'/entries/{entry["id"]}.json', {204})
            yield TickSyncStatus(sync_entry, {"id": entry["id"]}, 'delete')


def add_tick_entry(
    config: dict,
    entry: WorkLog,
    dry_run: bool,
) -> Iterable[TickSyncStatus]:
    """Add a new tick entry."""
    data = {
        "date": entry.start.isoformat(),
        "hours": entry.hours,
        "notes": entry.text,
        "task_id": entry.task_id,
        "user_id": config["user_id"],
    }
    if dry_run:
        yield TickSyncStatus(entry, data, 'add (dry run)')
    else:
        response = call(config, 'post', '/entries.json', {201}, data=data)
        yield TickSyncStatus(entry, response, 'add')


def sync_with_tick(
        config,
        entries: Iterable[WorkLog],
        dry_run=False) -> Iterable[TickSyncStatus]:
    def get_day(entry):
        return entry.start.date()
    for date, entries in itertools.groupby(entries, key=get_day):
        yield from remove_tick_data(config, date, dry_run)
        for entry in entries:
            yield from add_tick_entry(config, entry, dry_run)


def log_tick_sync(
        entries: Iterable[TickSyncStatus],
        ticklog) -> Iterable[TickSyncStatus]:
    with ticklog.open('a') as f:
        for entry, resp, action in entries:
            if action == 'error':
                comment = '; '.join(resp.get('errorMessages', []))
            else:
                comment = entry.text
            f.write(','.join(map(str, [
                get_now().isoformat(timespec='seconds'),
                entry.start.isoformat(timespec='minutes'),
                entry.hours,
                resp.get('id', ''),
                action,
                comment,
            ])) + '\n')

            yield TickSyncStatus(entry, resp, action)


class Date:

    def __init__(self, fmt='%Y-%m-%d'):
        self.fmt = fmt

    def __call__(self, value):
        if value.lower() == 'today':
            return datetime.datetime.now().astimezone().replace(
                hour=0, minute=0, second=0, microsecond=0)
        if value.lower() == 'yesterday':
            return (
                datetime.datetime.now() -
                datetime.timedelta(1)).astimezone().replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0)
        return datetime.datetime.strptime(value, self.fmt).astimezone()


def show_results(
        entries: Iterable[TickSyncStatus],
        stdout,
        verbose=0):
    totals = {
        'hours': collections.defaultdict(int),
        'entries': collections.defaultdict(int),
    }
    print(file=stdout)
    for entry, resp, action in entries:
        action = action.replace(' (dry run)', '')
        if action == 'add':
            print('ADD: {start} {amount:>8}: {comment}'.format(
                start=entry.start.isoformat(timespec='minutes'),
                amount=entry.hours,
                comment=entry.text,
            ), file=stdout)
            totals['hours'][entry.task] += entry.hours
            totals['entries'][entry.task] += 1
        elif action == 'error':
            print('ERR: {start} {amount:>8}: {comment}'.format(
                start=entry.start.isoformat(timespec='minutes'),
                amount=entry.hours,
                comment='; '.join(resp.get('errorMessages', [])),
            ), file=stdout)

    if totals['hours']:
        print(file=stdout)
        print('TOTALS:', file=stdout)
        for task, hours in sorted(totals['hours'].items()):
            entries = totals['entries'][task]
            print(f'{task}: {hours} h in {entries} entries.', file=stdout)


def _main(argv=None, stdout=sys.stdout):
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', default='~/.gtimelog/gtimelogrc')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='be more verbose (can be repeated)')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
        help="don't sync anything, just show what would be done")
    parser.add_argument('--since', type=Date(),
                        help="sync logs from specified yyyy-mm-dd date")
    parser.add_argument('--until', type=Date(),
                        help="sync logs up until specified yyyy-mm-dd date")
    args = parser.parse_args(argv)

    if args.since and args.until and args.since >= args.until:
        parser.error(
            f'the time interval is empty ({args.since} .. {args.until})')

    config_file = pathlib.Path(args.config).expanduser().resolve()
    try:
        config = read_config(config_file)
    except ConfigurationError as e:
        print('Error:', e, file=stdout)
        return 1

    with config['timelog'].open() as f:
        entries = read_timelog(f, midnight=config['midnight'])
        entries = parse_timelog(config, entries)
        entries = filter_timelog(entries, since=args.since, until=args.until)
        entries = sync_with_tick(config, entries, dry_run=args.dry_run)
        entries = log_tick_sync(entries, config['ticklog'])
        show_results(entries, stdout, verbose=args.verbose)


def main(argv=None, stdout=sys.stdout):
    try:
        return _main(argv=argv, stdout=stdout)
    except KeyboardInterrupt:
        sys.exit("Interrupted!")


if __name__ == '__main__':
    sys.exit(main())
