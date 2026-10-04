const http=require('http'),fs=require('fs'),path=require('path');
const root='/home/user/zabuton_skills/scraping-procedure-deck/evals/files/mock-shop/site';
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css'};
http.createServer((q,r)=>{let p=new URL(q.url,'http://x').pathname;if(p==='/')p='/index.html';
const f=path.join(root,p);if(!f.startsWith(root)||!fs.existsSync(f)){r.writeHead(404);return r.end('nf');}
r.writeHead(200,{'content-type':types[path.extname(f)]||'text/plain'});fs.createReadStream(f).pipe(r);}).listen(8123,'127.0.0.1');
