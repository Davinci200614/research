Using OAuth for user consent if a service account isn't feasible. So, I'll outline the steps for the user clearly:

Create a Google Cloud project and enable the Sheets API.
Set up a service account, generate a key JSON, and download it, then set the path in the environment variables.
Share the sheet with the service account's email, ensuring it has editor access.
Add the necessary environment variables: SHEET_ID, WORKSHEET_NAME, and GOOGLE_SA_JSON.
Modify the code in soundchart.py, possibly removing the CSV import while asking the user to ensure the header names match.