from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import re
from bs4 import BeautifulSoup
from colorthief import ColorThief
from io import BytesIO
from urllib.parse import urljoin, urlparse

# Initialize the FastAPI app (Render looks for this variable)
app = FastAPI(title="Brand Scraper API")

# Configure CORS so your frontend can communicate with it safely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define the expected JSON payload
class BrandRequest(BaseModel):
    targetUrl: str

def extract_brand_info(target_url: str):
    # --- 1. ROBUST URL HANDLING ---
    target_url = target_url.strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url
        
    # Extract just the main domain if they passed a deep link
    parsed_url = urlparse(target_url)
    main_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }

    try:
        response = requests.get(main_url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        base_url = response.url

        # Extract Name & Description
        og_name = soup.find("meta", property="og:site_name")
        title = soup.find("title")
        name = (og_name["content"] if og_name else None) or \
               (title.text.split('-')[0].strip() if title else None) or \
               urlparse(base_url).hostname.replace('www.', '').capitalize()

        og_desc = soup.find("meta", property="og:description")
        meta_desc = soup.find("meta", attrs={"name": "description"})
        description = (og_desc["content"] if og_desc else None) or \
                      (meta_desc["content"] if meta_desc else "")

        # Extract Avatar/Logo
        avatar = None
        icon_tags = [
            soup.find("meta", property="og:image"),
            soup.find("link", rel="apple-touch-icon"),
            soup.find("link", rel="icon"),
            soup.find("link", rel="shortcut icon")
        ]
        
        for tag in icon_tags:
            if tag:
                raw_url = tag.get("content") or tag.get("href")
                if raw_url:
                    avatar = urljoin(base_url, raw_url)
                    break
        
        if not avatar:
            avatar = f"https://logo.clearbit.com/{urlparse(base_url).hostname}"

        # Extract Colors
        primary_color = None
        color_palette = []
        theme_meta = soup.find("meta", attrs={"name": "theme-color"})
        if theme_meta:
            primary_color = theme_meta.get("content")

        secondary_color = "#F5F5F5"
        accent_color = "#333333"

        if avatar:
            try:
                img_response = requests.get(avatar, timeout=5)
                f = BytesIO(img_response.content)
                color_thief = ColorThief(f)
                
                dominant_rgb = color_thief.get_color(quality=1)
                if not primary_color:
                    primary_color = '#{:02x}{:02x}{:02x}'.format(*dominant_rgb)
                
                palette = color_thief.get_palette(color_count=5, quality=1)
                color_palette = ['#{:02x}{:02x}{:02x}'.format(*color) for color in palette]
                
                if len(color_palette) >= 2:
                    secondary_color = color_palette[1]
                if len(color_palette) >= 3:
                    accent_color = color_palette[2]
            except Exception:
                pass # Fail silently for colors, use defaults

        # --- 2. SMARTER FONT EXTRACTION ---
        font_family = "Inter, sans-serif" # The ultimate fallback
        found_fonts = []

        # A. Check for Google Fonts Links
        for link in soup.find_all("link", href=True):
            if "fonts.googleapis.com" in link["href"]:
                match = re.search(r'family=([^:&]+)', link["href"])
                if match:
                    font = match.group(1).replace('+', ' ')
                    found_fonts.append(f"'{font}', sans-serif")

        # B. Aggressively scan all <style> tags in the HTML
        for style in soup.find_all("style"):
            if style.string:
                # Regex to find "font-family: 'Roboto', sans-serif;"
                matches = re.findall(r'(?i)font-family\s*:\s*([^;\}]+)', style.string)
                for m in matches:
                    clean_font = m.strip().replace('"', "'")
                    # Ignore useless generic CSS variables or base classes
                    if clean_font and not clean_font.startswith('var(') and clean_font.lower() not in ['inherit', 'sans-serif', 'serif', 'monospace', 'initial']:
                        found_fonts.append(clean_font)
        
        # C. If we found actual fonts, use the first valid one we scraped
        if found_fonts:
            font_family = found_fonts[0]

        return {
            "description": description,
            "avatar": avatar,
            "branding": {
                "primary_color": primary_color or "#0F4C81",
                "secondary_color": secondary_color,
                "accent_color": accent_color,
                "color_palette": color_palette if color_palette else ["#0F4C81", "#F5F5F5", "#333333"],
                "font_family": font_family
            }
        }

    except Exception as e:
        return {"error": str(e)}

# Create the live API endpoint
@app.post("/api/organization/extract-brand")
async def extract_brand(request: BrandRequest):
    result = extract_brand_info(request.targetUrl)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result
