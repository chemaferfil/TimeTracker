const fs = require('fs'); 
const lines = fs.readFileSync('routes/admin.py', 'utf8').split(/\r?\n/); 
const start = lines.findIndex(l=
