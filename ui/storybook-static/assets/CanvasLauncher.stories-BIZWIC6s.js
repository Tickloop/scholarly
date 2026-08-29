import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{n as t}from"./iframe-CXgjsIO4.js";import{t as n}from"./jsx-runtime-DeHZSEgm.js";import{n as r,t as i}from"./researchData-CBM3CDD8.js";import{r as a,t as o}from"./utils-BPwTFDae.js";function s({canvas:e,papers:t,messages:n,selectedCanvasId:r,selectedPaper:i,activeAgent:o,isBusy:s,error:d,runStatus:f,runStatusLabel:p,onCreate:m,onAddPaperLink:h,onSendMessage:g,onClearPaper:_,onStop:v,onRetry:y,onUndo:b}){let[x,S]=(0,l.useState)(``),[C,w]=(0,l.useState)(),T=(0,l.useId)(),E=x.match(/(?:^|\s)@([^@]*)$/)?.[1].toLowerCase(),D=E===void 0?[]:t.filter(e=>e.title.toLowerCase().includes(E)).slice(0,5);async function O(e){e.preventDefault();let n=x.trim();if(!n){w(`Enter a research goal or paper link.`);return}w(void 0),S(``);try{if(r&&a(n)){await h(n);return}if(r){let e=t.filter(e=>n.includes(`@${e.title}`)).map(e=>e.id);await g(n,Array.from(new Set([...i?[i.id]:[],...e])));return}await m({name:c(n),research_goal:n})}catch{S(e=>e||n)}}return(0,u.jsxs)(`section`,{"aria-label":`Research chat`,children:[(0,u.jsxs)(`header`,{children:[(0,u.jsx)(`strong`,{children:e?.name??`New canvas`}),o?(0,u.jsxs)(`span`,{children:[` · Agent: `,o]}):null]}),n.length>0?(0,u.jsx)(`ol`,{className:`canvas-launcher__transcript`,"aria-label":`Conversation`,"aria-live":`polite`,children:n.map(e=>(0,u.jsxs)(`li`,{children:[(0,u.jsx)(`strong`,{children:e.role===`user`?`You`:e.agent_name}),`: `,e.content,e.status===`queued`?` (queued)`:``]},e.id))}):null,i?(0,u.jsxs)(`p`,{children:[`Paper context: `,i.title,` `,(0,u.jsx)(`button`,{type:`button`,onClick:_,children:`Remove`})]}):null,(0,u.jsx)(`form`,{onSubmit:O,children:(0,u.jsxs)(`fieldset`,{disabled:s,children:[(0,u.jsx)(`legend`,{children:r?`Message agents or add a paper`:`Build a research canvas`}),(0,u.jsxs)(`label`,{children:[r?`Message, @paper, or paper link`:`Research goal`,(0,u.jsx)(`textarea`,{name:`researchInput`,value:x,placeholder:r?`Ask the agent, mention @paper, or paste a paper link`:`Describe the research map to build`,"aria-autocomplete":`list`,"aria-controls":T,"aria-expanded":D.length>0,onChange:e=>{S(e.target.value),w(void 0)},required:!0})]}),(0,u.jsx)(`button`,{type:`submit`,children:r?`Send`:`Build canvas`})]})}),D.length>0?(0,u.jsx)(`ul`,{id:T,"aria-label":`Paper mentions`,children:D.map(e=>(0,u.jsx)(`li`,{children:(0,u.jsx)(`button`,{type:`button`,onClick:()=>{S(t=>t.replace(/@[^@]*$/,`@${e.title} `))},children:e.title})},e.id))}):null,s?(0,u.jsx)(`p`,{role:`status`,children:`Submitting…`}):null,f?(0,u.jsxs)(`p`,{role:`status`,children:[p??`Run`,`: `,f]}):null,C||d?(0,u.jsx)(`p`,{role:`alert`,children:C??d}):null,v?(0,u.jsx)(`button`,{type:`button`,onClick:v,children:`Stop`}):null,y?(0,u.jsx)(`button`,{type:`button`,onClick:y,children:`Retry`}):null,b?(0,u.jsx)(`button`,{type:`button`,onClick:b,children:`Undo`}):null]})}function c(e){return e.slice(0,80)}var l,u;function d(){return(d=e((()=>{l=t(),o(),u=n(),s.__docgenInfo={description:``,methods:[],displayName:`CanvasLauncher`}})))()}var f,p,m,h,g,_,v,y,b,x,S,C,w,T;function E(){return(E=e((()=>{i(),d(),{expect:f,fn:p,userEvent:m,within:h}=__STORYBOOK_MODULE_TEST__,g={id:`canvas-1`,name:`Transformer history`,research_goal:`Map the papers that led to modern transformers.`,build_status:`completed`,created_at:`2026-08-29T00:00:00Z`,updated_at:`2026-08-29T00:00:00Z`},_={title:`Research/CanvasLauncher`,component:s,args:{canvas:g,papers:r,messages:[],selectedCanvasId:`canvas-1`,isBusy:!1,onCreate:p(),onAddPaperLink:p(),onSendMessage:p(),onClearPaper:p()}},v={args:{canvas:void 0,selectedCanvasId:void 0},play:async({args:e,canvasElement:t})=>{let n=h(t);await f(n.getByRole(`region`,{name:`Research chat`})).toBeVisible(),await m.type(n.getByRole(`textbox`,{name:`Research goal`}),`Map retrieval augmented generation research`),await m.click(n.getByRole(`button`,{name:`Build canvas`})),await f(e.onCreate).toHaveBeenCalledWith({name:`Map retrieval augmented generation research`,research_goal:`Map retrieval augmented generation research`})}},y={args:{runStatus:`queued`,runStatusLabel:`Paper link run`}},b={args:{canvas:{...g,id:`canvas-2`,build_status:`idle`},selectedCanvasId:`canvas-2`,runStatus:`idle`,runStatusLabel:`Canvas build`},play:async({canvasElement:e})=>{let t=h(e);await f(t.getByText(`Canvas build: idle`)).toBeVisible(),await f(t.queryByText(`Canvas build: completed`)).toBeNull()}},x={args:{messages:[{id:`message-1`,canvas_id:`canvas-1`,role:`user`,agent_name:`main`,content:`Compare the strongest approaches.`,status:`queued`,created_at:`2026-08-29T00:00:00Z`}],runStatus:`review.completed`,runStatusLabel:`Paper link run`}},S={args:{error:`No verified academic metadata was found for the submitted link.`,runStatus:`run.failed`,runStatusLabel:`Paper link run`}},C={args:{runStatus:`run.completed`,runStatusLabel:`Canvas build run`,onRetry:p()},play:async({args:e,canvasElement:t})=>{let n=h(t);await m.click(n.getByRole(`button`,{name:`Retry`})),await f(e.onRetry).toHaveBeenCalledOnce()}},w={args:{selectedPaper:r[0],activeAgent:`reviewer`},play:async({args:e,canvasElement:t})=>{let n=h(t),i=n.getByRole(`textbox`,{name:`Message, @paper, or paper link`});await m.type(i,`@BERT`),await m.click(n.getByRole(`button`,{name:r[1].title})),await m.click(n.getByRole(`button`,{name:`Send`})),await f(e.onSendMessage).toHaveBeenCalledWith(`@${r[1].title}`,[r[0].id,r[1].id])}},v.parameters={...v.parameters,docs:{...v.parameters?.docs,source:{originalSource:`{
  args: {
    canvas: undefined,
    selectedCanvasId: undefined
  },
  play: async ({
    args,
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('region', {
      name: 'Research chat'
    })).toBeVisible();
    await userEvent.type(canvas.getByRole('textbox', {
      name: 'Research goal'
    }), 'Map retrieval augmented generation research');
    await userEvent.click(canvas.getByRole('button', {
      name: 'Build canvas'
    }));
    await expect(args.onCreate).toHaveBeenCalledWith({
      name: 'Map retrieval augmented generation research',
      research_goal: 'Map retrieval augmented generation research'
    });
  }
}`,...v.parameters?.docs?.source}}},y.parameters={...y.parameters,docs:{...y.parameters?.docs,source:{originalSource:`{
  args: {
    runStatus: 'queued',
    runStatusLabel: 'Paper link run'
  }
}`,...y.parameters?.docs?.source}}},b.parameters={...b.parameters,docs:{...b.parameters?.docs,source:{originalSource:`{
  args: {
    canvas: {
      ...canvas,
      id: 'canvas-2',
      build_status: 'idle'
    },
    selectedCanvasId: 'canvas-2',
    runStatus: 'idle',
    runStatusLabel: 'Canvas build'
  },
  play: async ({
    canvasElement
  }) => {
    const story = within(canvasElement);
    await expect(story.getByText('Canvas build: idle')).toBeVisible();
    await expect(story.queryByText('Canvas build: completed')).toBeNull();
  }
}`,...b.parameters?.docs?.source}}},x.parameters={...x.parameters,docs:{...x.parameters?.docs,source:{originalSource:`{
  args: {
    messages: [{
      id: 'message-1',
      canvas_id: 'canvas-1',
      role: 'user',
      agent_name: 'main',
      content: 'Compare the strongest approaches.',
      status: 'queued',
      created_at: '2026-08-29T00:00:00Z'
    }],
    runStatus: 'review.completed',
    runStatusLabel: 'Paper link run'
  }
}`,...x.parameters?.docs?.source}}},S.parameters={...S.parameters,docs:{...S.parameters?.docs,source:{originalSource:`{
  args: {
    error: 'No verified academic metadata was found for the submitted link.',
    runStatus: 'run.failed',
    runStatusLabel: 'Paper link run'
  }
}`,...S.parameters?.docs?.source}}},C.parameters={...C.parameters,docs:{...C.parameters?.docs,source:{originalSource:`{
  args: {
    runStatus: 'run.completed',
    runStatusLabel: 'Canvas build run',
    onRetry: fn()
  },
  play: async ({
    args,
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', {
      name: 'Retry'
    }));
    await expect(args.onRetry).toHaveBeenCalledOnce();
  }
}`,...C.parameters?.docs?.source}}},w.parameters={...w.parameters,docs:{...w.parameters?.docs,source:{originalSource:`{
  args: {
    selectedPaper: papers[0],
    activeAgent: 'reviewer'
  },
  play: async ({
    args,
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    const input = canvas.getByRole('textbox', {
      name: 'Message, @paper, or paper link'
    });
    await userEvent.type(input, '@BERT');
    await userEvent.click(canvas.getByRole('button', {
      name: papers[1].title
    }));
    await userEvent.click(canvas.getByRole('button', {
      name: 'Send'
    }));
    await expect(args.onSendMessage).toHaveBeenCalledWith(\`@\${papers[1].title}\`, [papers[0].id, papers[1].id]);
  }
}`,...w.parameters?.docs?.source}}},T=[`Idle`,`Queued`,`NewlyCreatedIdleCanvas`,`Processing`,`FailedLink`,`RecoverableCompletedBuild`,`PaperMention`]})))()}E();export{S as FailedLink,v as Idle,b as NewlyCreatedIdleCanvas,w as PaperMention,x as Processing,y as Queued,C as RecoverableCompletedBuild,T as __namedExportsOrder,_ as default};