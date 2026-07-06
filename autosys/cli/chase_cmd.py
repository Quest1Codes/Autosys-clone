import click
from rich.console import Console
from sqlalchemy import select
from autosys.db.connection import sync_session
from autosys.db.schema import JobRow
from autosys.models.enums import JobStatus
from autosys.agent.protocol import send_message, StatusRequest
from autosys.db.repository import machines as machine_repo

_console = Console()

@click.command(name="chase")
def chase():
    """Verify if STARTING/RUNNING jobs are actually alive on target machine."""
    with sync_session() as session:
        jobs = session.scalars(
            select(JobRow).where(JobRow.status.in_([JobStatus.STARTING, JobStatus.RUNNING]))
        ).all()
        
        if not jobs:
            _console.print("No jobs are currently STARTING or RUNNING.")
            return
            
        for job in jobs:
            if job.job_type == "BOX":
                _console.print(f"[cyan]CHASE[/cyan]: Job {job.job_name} is a BOX job in {job.status} state. (Skipping ping)")
                continue

            machine = job.machine or "localhost"
            machine_row = machine_repo.get(session, machine)
            if not machine_row:
                _console.print(f"[red]CHASE ORPHAN[/red]: Machine '{machine}' for job {job.job_name} is not registered.")
                continue

            try:
                resp = send_message(machine_row.host, machine_row.port, StatusRequest(), timeout=3.0)
                if resp.get("type") == "error":
                    _console.print(f"[red]CHASE ORPHAN[/red]: Job {job.job_name} on {machine} (Error: {resp.get('reason')})")
                    continue
                
                active_jobs = resp.get("active_jobs", [])
                if job.job_name in active_jobs:
                    _console.print(f"[green]CHASE ALIVE[/green]: Job {job.job_name} is actively running on {machine}.")
                else:
                    _console.print(f"[red]CHASE ORPHAN[/red]: Job {job.job_name} is NOT running on {machine}, but scheduler thinks it is {job.status}.")
            except Exception as e:
                _console.print(f"[red]CHASE ORPHAN[/red]: Machine {machine} unreachable ({e}). Job {job.job_name} might be orphaned.")
