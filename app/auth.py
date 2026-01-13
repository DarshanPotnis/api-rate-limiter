from fastapi import Header, HTTPException

VALID_API_KEYS = {
    "free-tier-key": "free_user",
    "pro-tier-key": "pro_user",
    "enterprise-key": "enterprise_user",
}

def get_api_key(x_api_key: str = Header(...)):
    if x_api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    return VALID_API_KEYS[x_api_key]
