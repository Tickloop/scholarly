import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{t}from"./jsx-runtime-DeHZSEgm.js";import{a as n,i as r}from"./PaperNode-BEx7mgbs.js";import{n as i,t as a}from"./Canvas-Cs133t-o.js";var o,s,c,l,u,d,f,p;function m(){return(m=e((()=>{i(),n(),o=t(),{expect:s,fireEvent:c,fn:l,waitFor:u}=__STORYBOOK_MODULE_TEST__,d={title:`Research/Canvas`,component:a,args:{onPaperLinkPaste:l()},render:e=>(0,o.jsx)(r,{papers:[],relationships:[],children:(0,o.jsx)(a,{...e})})},f={play:async({args:e,canvasElement:t})=>{let n=await u(()=>{let e=t.querySelector(`.react-flow`);return s(e).toBeTruthy(),e});await u(()=>{s(n.querySelector(`.react-flow__viewport`)).toBeTruthy()}),await u(()=>{s(t.querySelector(`[data-ready="true"]`)).toBeTruthy()}),c.pointerMove(n,{clientX:240,clientY:180});let r=new DataTransfer;r.setData(`text/plain`,`https://arxiv.org/abs/2005.11401`),t.ownerDocument.dispatchEvent(new ClipboardEvent(`paste`,{bubbles:!0,cancelable:!0,clipboardData:r})),await u(()=>s(e.onPaperLinkPaste).toHaveBeenCalledWith(`https://arxiv.org/abs/2005.11401`,s.objectContaining({x:s.any(Number),y:s.any(Number)})))}},f.parameters={...f.parameters,docs:{...f.parameters?.docs,source:{originalSource:`{
  play: async ({
    args,
    canvasElement
  }) => {
    const flow = await waitFor(() => {
      const element = canvasElement.querySelector('.react-flow');
      expect(element).toBeTruthy();
      return element as HTMLElement;
    });
    await waitFor(() => {
      expect(flow.querySelector('.react-flow__viewport')).toBeTruthy();
    });
    await waitFor(() => {
      expect(canvasElement.querySelector('[data-ready="true"]')).toBeTruthy();
    });
    fireEvent.pointerMove(flow, {
      clientX: 240,
      clientY: 180
    });
    const clipboardData = new DataTransfer();
    clipboardData.setData('text/plain', 'https://arxiv.org/abs/2005.11401');
    canvasElement.ownerDocument.dispatchEvent(new ClipboardEvent('paste', {
      bubbles: true,
      cancelable: true,
      clipboardData
    }));
    await waitFor(() => expect(args.onPaperLinkPaste).toHaveBeenCalledWith('https://arxiv.org/abs/2005.11401', expect.objectContaining({
      x: expect.any(Number),
      y: expect.any(Number)
    })));
  }
}`,...f.parameters?.docs?.source}}},p=[`PastePaperAtPointer`]})))()}m();export{f as PastePaperAtPointer,p as __namedExportsOrder,d as default};