import argparse
import os
import sys
import requests
import boto3
from urllib.parse import quote

def main():
    parser = argparse.ArgumentParser(description="Regenerate a report image using Pollinations AI and upload to R2.")
    parser.add_argument("--slug", required=True, help="Report slug")
    parser.add_argument("--image-key", required=True, help="Target image key, e.g. image-1.png")
    parser.add_argument("--prompt", required=True, help="Image description prompt")
    parser.add_argument("--r2-prefix", required=False, help="R2 prefix to upload to")
    args = parser.parse_args()

    r2_account_id = os.getenv("R2_ACCOUNT_ID")
    r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
    r2_bucket = os.getenv("R2_BUCKET")

    if not all([r2_account_id, r2_access_key_id, r2_secret_access_key, r2_bucket]):
        print("Error: Missing R2 environment variables.")
        sys.exit(1)

    raw_prompt = args.prompt.strip()
    # Apply high-end professional photography styling templates for maximum realism
    styled_prompt = (
        f"A stunning, highly realistic professional photograph of {raw_prompt}. "
        "Award-winning photojournalism style, captured on a high-end full-frame DSLR camera, "
        "50mm lens, f/8 aperture, natural realistic lighting, sharp focus, crisp details, "
        "authentic colors and textures, volumetric atmosphere, realistic surfaces, "
        "editorial magazine quality. No 3D render, no CGI, no cartoon, no vector, no drawing, no illustration"
    )
    print(f"Generating image with styled prompt: {styled_prompt}")
    
    # 1. Download image from Pollinations AI
    base_url = "https://image.pollinations.ai/prompt/"
    query = "?width=1536&height=1024&enhance=false&private=true&nologo=true&safe=true&model=flux"
    image_url = base_url + quote(styled_prompt, safe="") + query

    try:
        resp = requests.get(image_url, timeout=60, headers={"User-Agent": "GateXReportGenerator/1.0"})
        resp.raise_for_status()
        image_bytes = resp.content
        print(f"Downloaded generated image successfully ({len(image_bytes)} bytes).")
    except Exception as e:
        print(f"Error downloading image from Pollinations AI: {e}")
        sys.exit(1)

    # 2. Upload to R2
    s3_client = boto3.client(
        "s3",
        endpoint_url=f"https://{r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=r2_access_key_id,
        aws_secret_access_key=r2_secret_access_key,
        region_name="auto"
    )

    if args.r2_prefix:
        r2_prefix = args.r2_prefix
        if not r2_prefix.endswith("/"):
            r2_prefix += "/"
    else:
        r2_prefix = f"reports/{args.slug}/"
        
    r2_key = f"{r2_prefix}current/assets/{args.image_key}"
    print(f"Uploading to R2 key: {r2_key}")

    try:
        s3_client.put_object(
            Bucket=r2_bucket,
            Key=r2_key,
            Body=image_bytes,
            ContentType="image/png"
        )
        print("Uploaded successfully to R2.")
    except Exception as e:
        print(f"Error uploading to R2: {e}")
        sys.exit(1)

    # 3. Trigger Backend Webhook if BACKEND_URL and INTERNAL_TOKEN are set
    backend_url = os.getenv("BACKEND_URL")
    internal_token = os.getenv("INTERNAL_TOKEN")
    if backend_url and internal_token:
        url = f"{backend_url.rstrip('/')}/api/internal/events/image-regenerated"
        payload = {
            "document_id": args.slug,
            "image_key": args.image_key,
            "prompt": args.prompt
        }
        print(f"Triggering backend event: {url}")
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-internal-token": internal_token
                },
                timeout=10
            )
            print(f"Backend notified: {resp.status_code}")
        except Exception as e:
            print(f"Failed to notify backend: {e}")

if __name__ == "__main__":
    main()
