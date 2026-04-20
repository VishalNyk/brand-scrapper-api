from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re
from bs4 import BeautifulSoup
from colorthief import ColorThief
from io import BytesIO
from urllib.parse import urljoin, urlparse

# 1. Initialize the FastAPI app (Render looks for this)
app = FastAPI(
    title="Brand Scraper API",
    description="Microservice to extract brand colors, logos, and fonts from a URL."
)

# 2. Configure CORS (Crucial for your Web Frontend to avoid blocks)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Define the expected JSON payload
class BrandRequest(BaseModel):
    targetUrl: str

# 4. Your upgraded extraction logic
def extract_brand_info(target_url: str):
    target_url = target_url.strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url
        
    parsed_url = urlparse(target_url)
    main_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    domain = parsed_url.hostname.replace('www.', '')

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.google.com/'
    }

    # Defaults
    name = domain.capitalize()
    description = ""
    avatar = None
    primary_color = None
    secondary_color = "#F5F5F5"
    accent_color = "#333333"
    color_palette = []
    font_family = "Inter, sans-serif"

    # Flag to track if API failed to extract colors.
    used_default_color = True

    # --- 1. ATTEMPT HTML SCRAPE ---
    try:
        response = requests.get(main_url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = response.url

            og_name = soup.find("meta", property="og:site_name")
            title = soup.find("title")
            if og_name and og_name.get("content"): name = og_name["content"]
            elif title: name = title.text.split('-')[0].strip()

            og_desc = soup.find("meta", property="og:description")
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if og_desc and og_desc.get("content"): description = og_desc["content"]
            elif meta_desc and meta_desc.get("content"): description = meta_desc["content"]

            icon_tags = [
                soup.find("meta", property="og:image"),
                soup.find("link", rel="apple-touch-icon"),
                soup.find("link", rel="icon"),
                soup.find("link", rel="shortcut icon")
            ]
            for tag in icon_tags:
                if tag and (tag.get("content") or tag.get("href")):
                    raw_url = tag.get("content") or tag.get("href")
                    if not raw_url.endswith('.ico'):
                        avatar = urljoin(base_url, raw_url)
                        break

            theme_meta = soup.find("meta", attrs={"name": "theme-color"})
            if theme_meta: primary_color = theme_meta.get("content")

            found_fonts = []
            for style in soup.find_all("style"):
                if style.string:
                    matches = re.findall(r'(?i)font-family\s*:\s*([^;\}]+)', style.string)
                    for m in matches:
                        clean_font = m.strip().replace('"', "'")
                        if clean_font and not clean_font.startswith('var(') and clean_font.lower() not in ['inherit', 'sans-serif', 'serif', 'monospace', 'initial']:
                            found_fonts.append(clean_font)
            if found_fonts: font_family = found_fonts[0]
    except Exception as e:
        print(f"HTML Scrape failed for {domain}. Moving to Waterfall.")

    # --- 2. MULTI-PROVIDER WATERFALL ---
    avatar_sources = [
        avatar,
        f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
        f"https://logo.clearbit.com/{domain}",
        f"https://ui-avatars.com/api/?name={name}&background=random&color=fff&size=256"
    ]

    img_data = None
    final_avatar = None

    for source in avatar_sources:
        if not source: continue
        try:
            img_response = requests.get(source, timeout=5)
            if img_response.status_code == 200 and len(img_response.content) > 500:
                final_avatar = source
                img_data = img_response.content
                break 
        except Exception as e:
            print(f"Avatar fetch failed for {source}: {e}")

    if final_avatar:
        avatar = final_avatar

    # --- 3. BULLETPROOF COLOR EXTRACTION ---
    if img_data:
        if avatar and avatar.lower().endswith('.svg'):
            print(f"Skipping ColorMath for {domain} because avatar is an SVG.")
        else:
            try:
                f = BytesIO(img_data)
                color_thief = ColorThief(f)
            
                dominant_rgb = color_thief.get_color(quality=1)
                if not primary_color:
                    primary_color = '#{:02x}{:02x}{:02x}'.format(*dominant_rgb)
            
                palette = color_thief.get_palette(color_count=5, quality=1)
                color_palette = ['#{:02x}{:02x}{:02x}'.format(*color) for color in palette]
            
                if len(color_palette) >= 2: secondary_color = color_palette[1]
                if len(color_palette) >= 3: accent_color = color_palette[2]

                used_default_color = False
            except Exception as e:
                print(f"Color Math failed for {domain}: {e}")

    return {
        "name": name,
        "description": description,
        "avatar": avatar,
        "branding": {
            "primary_color": primary_color or "#0F4C81",
            "secondary_color": secondary_color,
            "accent_color": accent_color,
            "color_palette": color_palette if color_palette else ["#0F4C81", "#F5F5F5", "#333333"],
            "font_family": font_family,
            "is_default_color": used_default_color
        }
    }

# 5. The API Endpoint
@app.post("/api/organization/extract-brand")
async def extract_brand(request: BrandRequest):
    result = extract_brand_info(request.targetUrl)
    
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
        
    return result
