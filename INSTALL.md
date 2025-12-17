# Installation Instructions

## Python 3.8 Compatibility Fix

This bot requires `iracingdataapi>=1.3.0` for OAuth support, but version 1.3.0 uses Python 3.9+ type hints that are incompatible with Python 3.8.

If you're running Python 3.8 (common on cPanel shared hosting), follow these steps:

### Installation Steps

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Apply Python 3.8 compatibility patch:**
   ```bash
   python patch_iracingdataapi.py
   ```

3. **Run the bot:**
   ```bash
   python bot.py
   ```

### What the Patch Does

The `patch_iracingdataapi.py` script automatically:
- Finds your installed iracingdataapi package
- Converts Python 3.9+ type hints (`list[...]`, `dict[...]`) to Python 3.8 compatible versions (`List[...]`, `Dict[...]`)
- Adds necessary typing imports if missing
- Reports which files were modified

### One-Time Setup

You only need to run the patch script **once** after installing or upgrading iracingdataapi:
```bash
pip install --upgrade iracingdataapi
python patch_iracingdataapi.py
```

### For cPanel/Shared Hosting

After uploading your code to the server, activate your virtual environment first:
```bash
cd ~/iRacing_Results_Bot
source virtualenv/iRacing_Results_Bot/3.8/bin/activate
pip install -r requirements.txt
python patch_iracingdataapi.py  # Now python points to Python 3.8 in virtualenv
python bot.py
```

**Important:** Always activate the virtual environment first! Without it, `python` points to Python 2.x on your server.

## Troubleshooting

If you see errors like:
```
TypeError: 'type' object is not subscriptable
```

This means the patch wasn't applied. Run `python patch_iracingdataapi.py` again.

## Alternative: Upgrade Python

If possible, upgrading to Python 3.9 or later eliminates the need for this patch entirely.
