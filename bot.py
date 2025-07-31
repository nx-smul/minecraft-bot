import socket
from mcipc.rcon.je import Client
from mcstatus import JavaServer
from discord.ext import tasks, commands
import discord
import os
from dotenv import load_dotenv

load_dotenv()

# ====== ENV CONFIG ======
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
HOST_IP = os.environ.get("HOST_IP", "")
HOST_PORT = int(os.environ.get("HOST_PORT", 22))
SERVER_PORT = int(os.environ.get("SERVER_PORT", 25565))
QUERY_PORT = int(os.environ.get("QUERY_PORT", 25525))
RCON_PORT = int(os.environ.get("RCON_PORT", 25575))
RCON_PASS = os.environ.get("RCON_PASS", "")
LOOP_INTERVAL = int(os.environ.get("LOOP_INTERVAL", 60))
MC_VERSION = os.environ.get("MC_VERSION", "Unknown")
# =========================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
status_message: discord.Message | None = None


async def check_tcp_port(ip: str, port: int, label: str = "Port") -> bool:
    """Check if TCP port is reachable on given IP."""
    try:
        socket.create_connection((ip, port), timeout=5)
        print(f"[DEBUG] {label} {port} reachable on {ip}")
        return True
    except Exception as e:
        print(f"[WARN] {label} check failed: {e}")
        return False


async def fetch_status_message(channel: discord.TextChannel) -> discord.Message | None:
    """Fetch last bot message in channel to update it later."""
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return msg
    return None


def get_time_of_day_via_rcon(ip: str, port: int, password: str) -> str:
    """Query Minecraft server time of day via RCON."""
    if not password:
        print("[WARN] RCON password not set, skipping time query.")
        return "🚫 RCON not configured"
    try:
        with Client(ip, port) as client:
            client.login(password)
            response = client.run("time query daytime")
            ticks = int(response.strip().split()[-1])
            if ticks < 6000:
                return "🌞 Morning"
            elif ticks < 12000:
                return "☀️ Midday"
            elif ticks < 18000:
                return "🌇 Evening"
            else:
                return "🌙 Night"
    except Exception as e:
        print(f"[WARN] RCON time query failed: {type(e).__name__}: {e}")
        return "⚠️ RCON unavailable"


def format_status(
    mc_status: str,
    players: str,
    player_list: list[str],
    motd: str | None,
    time_label: str | None,
    game_mode: str | None,
    map_name: str | None,
    host_is_up: bool,
) -> str:
    """Format the server status message for Discord."""
    motd = motd or "Unknown"
    time_label = time_label or "Unknown"
    game_mode = game_mode or "Unknown"
    map_name = map_name or "Unknown"

    lines = [
        "# **Server Stats**",
        "",
        f"> **Minecraft Server**:",
        f"> {mc_status}",
    ]

    if mc_status.startswith("🟢"):
        lines.extend([
            f"> Players: {players}",
            f"> Online Players: {
                ', '.join(player_list) if player_list else '(none)'}",
            f"> MOTD: {motd}",
            f"> Time of Day: {time_label}",
            f"> Map: {map_name}",
            f"> Version: {MC_VERSION}",
            f"> Game Mode: {game_mode}",
        ])
    else:
        lines.append(f"> Version: {MC_VERSION}")

    lines.extend([
        "",
        f"> **Host Server**:",
        f"> {'🟢 Online' if host_is_up else '🔴 Offline'}",
        f"> Host IP: {HOST_IP}",
        f"> Host Port: {HOST_PORT}",
    ])

    return "\n".join(lines)


async def fetch_server_status() -> dict:
    """Fetch the status of the host and Minecraft server."""
    host_is_up = await check_tcp_port(HOST_IP, HOST_PORT, label="Host")

    mc_status = "🔴 Offline or Unreachable"
    motd = time_label = game_mode = map_name = None
    players = "None"
    player_list: list[str] = []

    if not host_is_up:
        return {
            "host_is_up": False,
            "mc_status": mc_status,
            "motd": motd,
            "time_label": time_label,
            "game_mode": game_mode,
            "map_name": map_name,
            "players": players,
            "player_list": player_list,
        }

    try:
        server = JavaServer(HOST_IP, SERVER_PORT, query_port=QUERY_PORT)
        status = await server.async_status()
        query = await server.async_query()

        mc_status = "🟢 Online"
        motd = getattr(query.motd, "raw", str(query.motd))
        players = f"{status.players.online}/{status.players.max}"
        player_list = query.players.list or []
        map_name = getattr(query, "map_name", "Unknown")
        game_mode = getattr(query.software, "gamemode", "Survival")
        time_label = get_time_of_day_via_rcon(HOST_IP, RCON_PORT, RCON_PASS)

    except Exception as e:
        print(f"[WARN] Server query failed: {type(e).__name__}: {e}")

    return {
        "host_is_up": host_is_up,
        "mc_status": mc_status,
        "motd": motd,
        "time_label": time_label,
        "game_mode": game_mode,
        "map_name": map_name,
        "players": players,
        "player_list": player_list,
    }


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await start_bot()


async def start_bot():
    global status_message
    try:
        channel = await bot.fetch_channel(CHANNEL_ID)
        status_message = await fetch_status_message(channel)
        if status_message is None:
            status_message = await channel.send("# **Server Stats**\n\n*Initializing...*")
        update_status.change_interval(seconds=LOOP_INTERVAL)
        update_status.start()
    except Exception as e:
        print(f"[CRITICAL] Startup failed: {e}")


@tasks.loop(seconds=LOOP_INTERVAL)
async def update_status():
    global status_message
    channel = await bot.fetch_channel(CHANNEL_ID)

    data = await fetch_server_status()

    if not data["host_is_up"]:
        fallback = "🔴 Host down — Eggroll fell off the grid. 🌐"
        try:
            if status_message:
                await status_message.edit(content=fallback)
            else:
                status_message = await channel.send(fallback)
        except Exception as e:
            print(f"[ERROR] Discord fallback failed: {e}")
        return

    message = format_status(
        data["mc_status"],
        data["players"],
        data["player_list"],
        data["motd"],
        data["time_label"],
        data["game_mode"],
        data["map_name"],
        data["host_is_up"],
    )

    try:
        print("[DEBUG] Final message:\n", message)
        if status_message:
            await status_message.edit(content=message)
        else:
            status_message = await channel.send(message)
        print("[DEBUG] Status updated")
    except Exception as e:
        print(f"[ERROR] Discord update failed: {e}")


@bot.command(name="mcstatus")
async def manual_status(ctx: commands.Context):
    data = await fetch_server_status()

    message = format_status(
        data["mc_status"],
        data["players"],
        data["player_list"],
        data["motd"],
        data["time_label"],
        data["game_mode"],
        data["map_name"],
        data["host_is_up"],
    )
    await ctx.send(message)


if __name__ == "__main__":
    try:
        print("[DEBUG] Launching Eggroll...")
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"[CRITICAL] Bot crashed: {e}")
