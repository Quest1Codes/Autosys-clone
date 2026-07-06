import click
from rich.console import Console

_console = Console()

@click.command(name="autoping")
@click.option("-m", "--machine", required=True, help="Machine name to ping")
def autoping(machine: str):
    """Verify connectivity between server, agent, and client."""
    _console.print(f"[green]AUTOPING[/green]: Verifying connectivity to {machine}...")
    _console.print("Server to Agent: SUCCESS (Stub)")
    _console.print("Agent to Server: SUCCESS (Stub)")
    _console.print("Client to Server: SUCCESS (Stub)")
