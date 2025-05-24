import os
import discord
from discord.ext import commands
import yt_dlp
import asyncio
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
import ssl
from discord.ui import View, Button, Select

# Load environment variables
load_dotenv()

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Spotify configuration
spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=spotify_client_id,
    client_secret=spotify_client_secret
))

# Music queue
queues = {}

# YT-DLP options
ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': True,
    'quiet': False,
    'no_warnings': False,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'force_ipv4': True,
    'cookiefile': 'youtube_cookies.txt'
}


ffmpeg_options = {
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

ssl._create_default_https_context = ssl._create_unverified_context

if os.getenv('YOUTUBE_COOKIES'):
    with open('youtube_cookies.txt', 'w', encoding='utf-8') as f:
        f.write(os.getenv('YOUTUBE_COOKIES'))

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]
            
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

async def play_next(ctx):
    if queues[ctx.guild.id]:
        url = queues[ctx.guild.id].pop(0)
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        await add_to_history(ctx, player)
        await send_now_playing_embed(ctx, player)

class NowPlayingView(View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label='⏸️ Dur', style=discord.ButtonStyle.danger, custom_id='pause')
    async def pause(self, interaction: discord.Interaction, button: Button):
        if interaction.user.voice and interaction.user.voice.channel == self.ctx.voice_client.channel:
            if self.ctx.voice_client and self.ctx.voice_client.is_playing():
                self.ctx.voice_client.pause()
                await interaction.response.send_message('⏸️ Müzik durduruldu! Devam etmek için "Devam" butonuna bas.', ephemeral=True)
            else:
                await interaction.response.send_message('❌ Çalan bir şarkı yok!', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Ses kanalında olmalısın!', ephemeral=True)

    @discord.ui.button(label='▶️ Devam', style=discord.ButtonStyle.success, custom_id='resume')
    async def resume(self, interaction: discord.Interaction, button: Button):
        if interaction.user.voice and interaction.user.voice.channel == self.ctx.voice_client.channel:
            if self.ctx.voice_client and self.ctx.voice_client.is_paused():
                self.ctx.voice_client.resume()
                await interaction.response.send_message('▶️ Müzik devam ediyor!', ephemeral=True)
            else:
                await interaction.response.send_message('❌ Beklemede bir şarkı yok!', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Ses kanalında olmalısın!', ephemeral=True)

    @discord.ui.button(label='⏭️ Geç', style=discord.ButtonStyle.primary, custom_id='gec')
    async def gec(self, interaction: discord.Interaction, button: Button):
        if interaction.user.voice and interaction.user.voice.channel == self.ctx.voice_client.channel:
            queue = queues.get(self.ctx.guild.id, [])
            if self.ctx.voice_client and (self.ctx.voice_client.is_playing() or self.ctx.voice_client.is_paused()):
                if not queue:
                    await interaction.response.send_message('⏭️ Atlanacak şarkı yok!', ephemeral=True)
                else:
                    self.ctx.voice_client.stop()
                    await interaction.response.send_message('⏭️ Şarkı atlandı!', ephemeral=True)
            else:
                await interaction.response.send_message('❌ Çalan bir şarkı yok!', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Ses kanalında olmalısın!', ephemeral=True)

    @discord.ui.button(label='❌ Kapat', style=discord.ButtonStyle.secondary, custom_id='kapat')
    async def kapat(self, interaction: discord.Interaction, button: Button):
        if interaction.user.voice and interaction.user.voice.channel == self.ctx.voice_client.channel:
            if self.ctx.voice_client:
                if self.ctx.guild.id in queues:
                    queues[self.ctx.guild.id] = []
                await self.ctx.voice_client.disconnect()
                await interaction.response.send_message('👋 Bot kanaldan atıldı!', ephemeral=True)
            else:
                await interaction.response.send_message('❌ Bot zaten bir ses kanalında değil!', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Ses kanalında olmalısın!', ephemeral=True)

class SearchSelectView(View):
    def __init__(self, ctx, results):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.results = results
        self.value = None
        options = [
            discord.SelectOption(label=entry['title'][:100], value=str(i), description=entry.get('uploader', '')[:100])
            for i, entry in enumerate(results)
        ]
        self.add_item(Select(placeholder='Bir şarkı seç...', options=options, custom_id='search_select'))

    @discord.ui.select(custom_id='search_select')
    async def select_callback(self, select, interaction: discord.Interaction):
        idx = int(select.values[0])
        self.value = idx
        await interaction.response.send_message(f'🎵 Seçilen şarkı: {self.results[idx]["title"]}', ephemeral=True)
        self.stop()

async def send_now_playing_embed(ctx, player):
    now_playing = f"**Şimdi Çalıyor:**\n{player.title}"
    queue = queues.get(ctx.guild.id, [])
    if queue:
        max_show = 5
        shown = queue[:max_show]
        queue_list = "\n".join([f"{i+1}. {url.replace('ytsearch:', '')}" for i, url in enumerate(shown)])
        if len(queue) > max_show:
            queue_list += f"\n...ve {len(queue) - max_show} şarkı daha..."
        queue_text = f"\n\n**Sıradaki Şarkılar:**\n{queue_list}"
    else:
        queue_text = "\n\n**Sıradaki Şarkılar:**\nYok"
    embed = discord.Embed(title='Müzik Bilgisi', description=now_playing + queue_text, color=0x1DB954)
    if hasattr(player, 'data') and 'thumbnail' in player.data:
        embed.set_thumbnail(url=player.data['thumbnail'])
    embed.set_footer(text='KOCHAN Müzik Botu')
    view = NowPlayingView(ctx)
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_ready():
    await bot.change_presence(
        activity=discord.Game(name="!komut")
    )
    print(f'{bot.user} olarak giriş yapıldı!')

# Spotify linkini temizle
def clean_spotify_url(url):
    # Her türlü Spotify track veya playlist linkinden sadece ana kısmı al
    match = re.search(r"spotify\.com/(track|playlist)/([a-zA-Z0-9]+)", url)
    if match:
        return f"https://open.spotify.com/{match.group(1)}/{match.group(2)}"
    # Eğer embed veya başka bir format varsa, ID'yi bulup ana linki oluştur
    match = re.search(r"/(track|playlist)/([a-zA-Z0-9]+)", url)
    if match:
        return f"https://open.spotify.com/{match.group(1)}/{match.group(2)}"
    return url

@bot.command(name='başla')
async def başla(ctx, *, url):
    if not ctx.author.voice:
        return await ctx.send("Bir ses kanalında olmalısın!")
    
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    
    # Spotify link kontrolü
    if 'spotify.com' in url:
        url = clean_spotify_url(url)
        try:
            if 'track' in url:
                track = sp.track(url)
                search_query = f"{track['name']} {track['artists'][0]['name']}"
                url = f"ytsearch:{search_query}"
            elif 'playlist' in url:
                playlist = sp.playlist(url)
                tracks = playlist['tracks']['items']
                for track in tracks:
                    track_name = track['track']['name']
                    artist_name = track['track']['artists'][0]['name']
                    search_query = f"{track_name} {artist_name}"
                    if ctx.guild.id not in queues:
                        queues[ctx.guild.id] = []
                    queues[ctx.guild.id].append(f"ytsearch:{search_query}")
                await ctx.send(f"🎵 Playlist kuyruğa eklendi: {playlist['name']}")
                if not ctx.voice_client.is_playing():
                    await play_next(ctx)
                return
        except Exception as e:
            await ctx.send(f"Spotify linki işlenirken hata oluştu: {str(e)}")
            return

    if ctx.guild.id not in queues:
        queues[ctx.guild.id] = []
    
    if ctx.voice_client.is_playing():
        queues[ctx.guild.id].append(url)
        await ctx.send("🎵 Şarkı kuyruğa eklendi!")
    else:
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        await add_to_history(ctx, player)
        await send_now_playing_embed(ctx, player)

@bot.command(name='geç')
async def geç(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Şarkı atlandı!")
    else:
        await ctx.send("❌ Çalan bir şarkı yok!")

@bot.command(name='dur')
async def dur(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏹️ Müzik durduruldu!")
    else:
        await ctx.send("❌ Çalan bir şarkı yok!")

@bot.command(name='beklet')
async def beklet(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send('⏸️ Müzik beklemeye alındı!')
    else:
        await ctx.send('❌ Çalan bir şarkı yok!')

@bot.command(name='devam')
async def devam(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send('▶️ Müzik devam ediyor!')
    else:
        await ctx.send('❌ Beklemede bir şarkı yok!')

@bot.command(name='at')
async def at(ctx):
    if ctx.voice_client:
        if ctx.guild.id in queues:
            queues[ctx.guild.id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Bot kanaldan atıldı!")
    else:
        await ctx.send("❌ Bot zaten bir ses kanalında değil!")

@bot.command(name='sıra')
async def sıra(ctx):
    if ctx.guild.id in queues and queues[ctx.guild.id]:
        queue_list = "\n".join([f"{i+1}. {url}" for i, url in enumerate(queues[ctx.guild.id])])
        embed = discord.Embed(title="Sıradaki Şarkılar", description=queue_list, color=0x1DB954)
        await ctx.send(embed=embed)
    else:
        await ctx.send("📋 Sırada şarkı yok!")

@bot.command(name='günlük')
async def gunluk(ctx):
    # Gerçek API ile entegre etmek için ilgili kütüphaneler ve anahtarlar eklenmeli
    # Şimdilik örnek/mock veri ile gösteriyorum
    hava = 'İstanbul: 23°C, Parçalı Bulutlu'
    borsa = 'BIST 100: 10.500 (+%0.8)\nUSD/TRY: 32.20\nEUR/TRY: 34.80'
    ozet = 'Bugün Türkiye genelinde hava sıcaklıkları mevsim normallerinde. Borsa yükselişte. Gündemde önemli bir gelişme yok.'
    embed = discord.Embed(title='Günlük Özet', color=0x1DB954)
    embed.add_field(name='🌤️ Hava Durumu', value=hava, inline=False)
    embed.add_field(name='💹 Borsa', value=borsa, inline=False)
    embed.add_field(name='📰 Gündem', value=ozet, inline=False)
    embed.set_footer(text='KOCHAN Günlük Bilgi Servisi')
    await ctx.send(embed=embed)

@bot.command(name='komut')
async def komut(ctx):
    embed = discord.Embed(title="Kullanabileceğiniz Komutlar", color=0x1DB954)
    embed.add_field(name="!başla <link>", value="YouTube veya Spotify linkinden müzik çalar", inline=False)
    embed.add_field(name="!geç", value="Çalan şarkıyı atlar", inline=False)
    embed.add_field(name="!dur", value="Çalan müziği durdurur", inline=False)
    embed.add_field(name="!devam", value="Bekleyen müziği devam ettirir", inline=False)
    embed.add_field(name="!beklet", value="Müziği beklemeye alır", inline=False)
    embed.add_field(name="!at", value="Botu ses kanalından atar", inline=False)
    embed.add_field(name="!sıra", value="Sıradaki şarkıları gösterir", inline=False)
    embed.add_field(name="!geçmiş", value="Son çalınan şarkıları gösterir", inline=False)
    embed.add_field(name="!günlük", value="Hava durumu, borsa ve gündem özeti", inline=False)
    embed.add_field(name="!komut", value="Tüm komutları listeler", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='ses')
async def ses(ctx, seviye: int):
    if not ctx.voice_client:
        return await ctx.send('Bot bir ses kanalında değil!')
    if seviye < 0 or seviye > 100:
        return await ctx.send('Ses seviyesi 0 ile 100 arasında olmalı!')
    ctx.voice_client.source.volume = seviye / 100
    await ctx.send(f'🔊 Ses seviyesi {seviye}% olarak ayarlandı.')

@bot.command(name='ara')
async def ara(ctx, *, arama):
    await ctx.send('🔎 Şarkılar aranıyor...')
    loop = bot.loop
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f'ytsearch5:{arama}', download=False))
    entries = data.get('entries') if data else None
    results = [entry for entry in (entries or []) if entry]
    results = results[:5]
    if not results:
        await ctx.send('Şarkı bulunamadı!')
        return
    desc = '\n'.join([f'{i+1}. {entry["title"]}' for i, entry in enumerate(results)])
    embed = discord.Embed(title='Arama Sonuçları', description=desc, color=0x1DB954)
    view = SearchSelectView(ctx, results)
    if not view.children or not getattr(view.children[0], 'options', []):
        await ctx.send('Şarkı bulunamadı!')
        return
    msg = await ctx.send(embed=embed, view=view)
    await view.wait()
    if view.value is not None:
        url = results[view.value]['webpage_url']
        if ctx.guild.id not in queues:
            queues[ctx.guild.id] = []
        if ctx.voice_client and ctx.voice_client.is_playing():
            queues[ctx.guild.id].append(url)
            await ctx.send('🎵 Seçilen şarkı kuyruğa eklendi!')
        else:
            if not ctx.voice_client:
                await ctx.author.voice.channel.connect()
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
            await add_to_history(ctx, player)
            await send_now_playing_embed(ctx, player)
    await msg.edit(view=None)

# Şarkı geçmişi için global bir liste
song_history = {}

# Şarkı geçmişi ekle
async def add_to_history(ctx, player):
    gid = ctx.guild.id
    if gid not in song_history:
        song_history[gid] = []
    song_history[gid].append(player.title)
    # Sadece son 20 şarkıyı tut
    if len(song_history[gid]) > 20:
        song_history[gid] = song_history[gid][-20:]

# !geçmiş komutu
@bot.command(name='geçmiş')
async def geçmiş(ctx):
    gid = ctx.guild.id
    if gid not in song_history or not song_history[gid]:
        await ctx.send('Henüz geçmişte şarkı yok!')
        return
    desc = '\n'.join([f'{i+1}. {title}' for i, title in enumerate(song_history[gid])])
    embed = discord.Embed(title='Son Çalınan Şarkılar', description=desc, color=0x1DB954)
    await ctx.send(embed=embed)

# Otomatik temizleme: 5 dakika boyunca müzik çalmazsa kanaldan ayrıl
import asyncio
async def auto_disconnect(vc, ctx):
    await asyncio.sleep(300)  # 5 dakika
    if not vc.is_playing() and not vc.is_paused():
        await vc.disconnect()
        await ctx.send('5 dakika boyunca müzik çalınmadığı için bot kanaldan ayrıldı.')

# başla ve play_next fonksiyonlarında çağır
async def start_auto_disconnect(ctx):
    if ctx.voice_client:
        bot.loop.create_task(auto_disconnect(ctx.voice_client, ctx))

# Bot token'ını .env dosyasından al
bot.run(os.getenv('DISCORD_TOKEN')) 