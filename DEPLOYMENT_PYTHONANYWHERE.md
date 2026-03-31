# Deploy Clinical Scheduler on PythonAnywhere

This guide walks through deploying your Flask scheduler on PythonAnywhere's free tier.

## Step 1: Create PythonAnywhere Account

1. Go to https://www.pythonanywhere.com
2. Click **Sign up** → Select **Beginner** account (free)
3. Enter email and password, verify
4. Log in to dashboard

## Step 2: Upload Your Code

### Option A: Via GitHub (Recommended)
1. In PythonAnywhere dashboard, click **Web**
2. Click **+ Add a new web app**
3. Choose **Flask** → **Python 3.10** (or latest available)
4. PythonAnywhere creates a starter Flask app; we'll replace it

### Option B: Via Upload
1. In dashboard, click **Files**
2. Upload your repo files or clone via Bash console

## Step 3: Clone Your GitHub Repository (Best)

1. Open **Bash console** in PythonAnywhere
2. Run:
   ```bash
   cd ~
   git clone https://github.com/Srivans7/Clinical-Scheduler.git
   cd Clinical-Scheduler
   ```
3. Install dependencies:
   ```bash
   mkvirtualenv --python=/usr/bin/python3.10 scheduler
   pip install -r requirements.txt
   ```

## Step 4: Configure Web App

1. Go to **Web** tab in dashboard
2. Click your web app (e.g., `yourusername.pythonanywhere.com`)
3. Under **Code section**, set:
   - **Source code**: `/home/yourusername/Clinical-Scheduler`
   - **Working directory**: `/home/yourusername/Clinical-Scheduler`

4. Under **Virtualenv**, set to: `/home/yourusername/.virtualenvs/scheduler`

5. Under **WSGI configuration file**, click to edit `/home/yourusername/mysite/wsgi_file.py`
   - Replace its full content with:
   ```python
   import sys
   project_dir = '/home/yourusername/Clinical-Scheduler'
   sys.path.insert(0, project_dir)
   from app import app as application
   ```
   - Save

6. Scroll to top and click **Reload** (green button)

## Step 5: Set Environment Variables (Optional for Production)

1. In Bash console:
   ```bash
   export FLASK_DEBUG=0
   ```
2. Or edit your app to read from PythonAnywhere's environment

## Step 6: Test Your App

1. Visit: `https://yourusername.pythonanywhere.com`
2. You should see the Clinical Scheduler home page
3. Try uploading a file and running a schedule

## Step 7: Troubleshooting

### Check Logs
- Click **Web** → **Log files** → view `error.log` and `server.log`

### Common Issues
- **ModuleNotFoundError**: Virtualenv not set correctly (Step 4)
- **Static files missing**: WSGI config wrong (Step 4)
- **Database locked**: SQLite concurrency; PythonAnywhere handles this

### Restart Web App
- Click **Web** → **Reload** button at top

## Step 8: Backup & Security

1. Database file (`data/audit_store.json`) is persisted between reloads
2. Keep GitHub repo updated for easy redeploy
3. PythonAnywhere free tier is sufficient for small teams

## Your Live URL

Once deployed:
- **App URL**: `https://yourusername.pythonanywhere.com`
- **Health check**: `https://yourusername.pythonanywhere.com/api/health`

## Free Tier Limits

- 512 MB disk storage
- 1 interactive console session
- 100 seconds CPU per day
- 1 web app

For your scheduler, this is plenty for development/demo use.

---

**Next steps after successful deploy:**
- Share live URL with team
- Monitor error logs weekly
- Upgrade to paid tier if you exceed 100 CPU seconds/day
