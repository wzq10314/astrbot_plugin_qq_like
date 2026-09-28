'use strict';
const $ = id => document.getElementById(id);
const key = 'reader:' + location.pathname;
let data, current = 0, mode = 'scroll', observer;
const store = {get(k){try{return localStorage.getItem(k)}catch{return null}},set(k,v){try{localStorage.setItem(k,String(v))}catch{}}};
function theme(dark){document.body.classList.toggle('dark',dark);$('theme').querySelector('span').textContent=dark?'浅色':'深色';store.set('reader-theme',dark?'dark':'light')}
theme(store.get('reader-theme')==='dark');
$('theme').onclick=()=>theme(!document.body.classList.contains('dark'));
function update(index){current=index;$('position').textContent=`${index+1} / ${data.pages.length}`;$('seek').value=index+1;$('singlePosition').textContent=`第 ${index+1} / ${data.pages.length} 页`;$('prev').disabled=index===0;$('next').disabled=index===data.pages.length-1;store.set(key,index);$('chapter').value=data.pages[index].chapter}
function figure(index){const p=data.pages[index],f=document.createElement('figure');f.className='page';f.id='page-'+index;f.dataset.index=index;const img=document.createElement('img');img.alt=`${p.chapter} · 第 ${index+1} 页`;img.loading=index<2||mode==='single'?'eager':'lazy';img.decoding='async';img.src='page/'+index;img.onerror=()=>{f.classList.add('load-error');if(f.querySelector('.retry'))return;const box=document.createElement('div');box.className='retry';box.textContent='这一页暂时未能加载。';const retry=document.createElement('button');retry.textContent='重新加载';retry.onclick=()=>{box.remove();f.classList.remove('load-error');img.src='page/'+index+'?retry='+Date.now()};box.append(retry);f.prepend(box)};f.append(img);
if(data.kind==='pixiv'){
 const details=document.createElement('figcaption');details.className='original-details';
 const heading=document.createElement('strong');heading.textContent=p.title||p.name;
 const header=document.createElement('div');header.className='original-heading';
 const number=document.createElement('span');number.className='original-number';number.textContent=String(index+1).padStart(2,'0')+' / '+data.pages.length;number.setAttribute('aria-label',`第 ${index+1} 张，共 ${data.pages.length} 张`);header.append(heading,number);
 const info=document.createElement('p');info.textContent=[p.artist,p.width&&p.height?`${p.width} × ${p.height}`:'',p.size?`${(p.size/1048576).toFixed(2)} MB`:''].filter(Boolean).join(' · ');
 const links=document.createElement('div');links.className='original-actions';
 const view=document.createElement('a');view.href='page/'+index;view.target='_blank';view.rel='noopener noreferrer';view.textContent='原尺寸查看 ↗';
 const save=document.createElement('a');save.href='original/'+index;save.download=p.name;save.textContent='下载原图 ↓';
 links.append(view,save);
 if(/^\d+$/.test(p.pid||'')){const source=document.createElement('a');source.href='https://www.pixiv.net/artworks/'+p.pid;source.target='_blank';source.rel='noopener noreferrer';source.textContent='作品来源 ↗';links.append(source)}
 details.append(header,info,links);f.append(details)
}return f}
function render(){if(observer)observer.disconnect();$('pages').replaceChildren();$('singleNav').hidden=mode!=='single';$('scrollMode').classList.toggle('selected',mode==='scroll');$('singleMode').classList.toggle('selected',mode==='single');$('scrollMode').setAttribute('aria-pressed',String(mode==='scroll'));$('singleMode').setAttribute('aria-pressed',String(mode==='single'));if(mode==='single'){$('pages').append(figure(current))}else{const fragment=document.createDocumentFragment();data.pages.forEach((_,i)=>fragment.append(figure(i)));$('pages').append(fragment);observer=new IntersectionObserver(entries=>{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>Math.abs(a.boundingClientRect.top)-Math.abs(b.boundingClientRect.top));if(visible.length)update(Number(visible[0].target.dataset.index))},{rootMargin:'-5% 0px -40% 0px',threshold:0});document.querySelectorAll('.page').forEach(f=>observer.observe(f))}update(current)}
function go(index){index=Math.max(0,Math.min(data.pages.length-1,index));update(index);if(mode==='single'){render();$('pages').scrollIntoView({behavior:'smooth',block:'start'})}else{$('page-'+index).scrollIntoView({behavior:'smooth',block:'start'})}}
function changeMode(value){if(!data)return;const saved=current;mode=value;store.set('reader-mode',mode);render();go(saved)}
$('scrollMode').onclick=()=>changeMode('scroll');$('singleMode').onclick=()=>changeMode('single');$('prev').onclick=()=>go(current-1);$('next').onclick=()=>go(current+1);$('seek').onchange=e=>go(Number(e.target.value)-1);$('toTop').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});$('chapter').onchange=e=>go(data.pages.findIndex(p=>p.chapter===e.target.value));document.addEventListener('keydown',e=>{if(mode!=='single'||!data||['INPUT','SELECT','BUTTON'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft')go(current-1);if(e.key==='ArrowRight')go(current+1)});
async function start(){try{const response=await fetch('manifest.json',{cache:'no-store'});if(!response.ok)throw Error('阅读链接不存在或已过期。请回到机器人重新生成。');data=await response.json();if(!Array.isArray(data.pages)||!data.pages.length)throw Error('这份内容暂时没有可阅读的页面。');$('title').textContent=data.title;document.title=data.title+' · 阅读室';$('subtitle').textContent='顺着画面，慢慢读完这段故事。';$('count').textContent=data.pages.length+' 页';$('expiry').textContent='有效至 '+new Date(data.expires*1000).toLocaleDateString('zh-CN');$('seek').max=data.pages.length;
if(data.kind==='pixiv'){
 document.body.classList.add('gallery');$('galleryInfo').hidden=false;
 $('subtitle').textContent='画面可以适配屏幕，原图不打折。';$('count').textContent=data.pages.length+' 张原图';
 document.querySelector('label[for=chapter]').textContent='作品';
 if(data.failed){$('notice').hidden=false;$('notice').textContent=`已获取 ${data.pages.length} / ${data.requested} 张原图。其余原图未获取成功，未用预览图替代。`}
}
const chapters=[...new Set(data.pages.map(p=>p.chapter))];$('chapter').replaceChildren(...chapters.map((name,i)=>{const o=document.createElement('option');o.value=name;o.textContent=chapters.length===1&&name==='正文'?'完整章节':name;return o}));let saved=Number(store.get(key));if(!Number.isInteger(saved)||saved<0||saved>=data.pages.length)saved=0;mode=store.get('reader-mode')==='single'?'single':'scroll';render();if(saved>0){$('resume').hidden=false;$('resumeText').textContent=`上次读到第 ${saved+1} 页`;$('resumeButton').onclick=()=>{go(saved);$('resume').hidden=true}}$('pages').setAttribute('aria-busy','false');$('dock').hidden=false}catch(e){$('title').textContent='暂时无法打开';$('subtitle').textContent='这段故事，稍后再继续。';$('count').textContent='链接不可用';$('notice').textContent=e.message;$('notice').hidden=false;$('pages').setAttribute('aria-busy','false')}}
start();
