import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{n as t}from"./iframe-CXgjsIO4.js";import{t as n}from"./jsx-runtime-DeHZSEgm.js";function r({canvases:e,selectedCanvasId:t,isBusy:n,onSelect:r,onRename:o,onDelete:s}){let c=e.find(e=>e.id===t),[l,u]=(0,i.useState)(c?.name??``);function d(e){e.preventDefault(),t&&l.trim()&&o(t,l.trim())}return(0,a.jsx)(`nav`,{"aria-label":`Canvas switcher`,children:(0,a.jsxs)(`details`,{children:[(0,a.jsx)(`summary`,{children:`Canvases`}),(0,a.jsxs)(`label`,{children:[`Open canvas`,(0,a.jsxs)(`select`,{value:t??``,onChange:e=>r(e.target.value||void 0),disabled:n,children:[(0,a.jsx)(`option`,{value:``,children:`New canvas`}),e.map(e=>(0,a.jsx)(`option`,{value:e.id,children:e.name},e.id))]})]}),t?(0,a.jsxs)(`form`,{"aria-label":`Rename or delete canvas`,onSubmit:d,children:[(0,a.jsxs)(`label`,{children:[`Canvas name`,(0,a.jsx)(`input`,{value:l,onChange:e=>u(e.target.value),required:!0})]}),(0,a.jsx)(`button`,{type:`submit`,disabled:n,children:`Rename`}),(0,a.jsx)(`button`,{type:`button`,disabled:n,onClick:()=>void s(t),children:`Delete`})]}):null]})})}var i,a;function o(){return(o=e((()=>{i=t(),a=n(),r.__docgenInfo={description:``,methods:[],displayName:`CanvasDrawer`}})))()}var s,c,l,u,d,f,p,m;function h(){return(h=e((()=>{o(),{expect:s,fn:c,userEvent:l,within:u}=__STORYBOOK_MODULE_TEST__,d={title:`Research/CanvasDrawer`,component:r,args:{canvases:[{id:`canvas-1`,name:`Transformer history`,research_goal:`Map transformer research.`,build_status:`completed`,created_at:`2026-08-29T00:00:00Z`,updated_at:`2026-08-29T00:00:00Z`},{id:`canvas-2`,name:`Diffusion models`,research_goal:`Map diffusion research.`,build_status:`running`,created_at:`2026-08-29T00:00:00Z`,updated_at:`2026-08-29T00:00:00Z`}],selectedCanvasId:`canvas-1`,isBusy:!1,onSelect:c(),onRename:c(),onDelete:c()}},f={play:async({args:e,canvasElement:t})=>{let n=u(t);await s(n.getByRole(`navigation`,{name:`Canvas switcher`})).toBeVisible(),await l.click(n.getByText(`Canvases`));let r=n.getByRole(`textbox`,{name:`Canvas name`});await l.clear(r),await l.type(r,`Renamed canvas`),await l.click(n.getByRole(`button`,{name:`Rename`})),await s(e.onRename).toHaveBeenCalledWith(`canvas-1`,`Renamed canvas`)}},p={args:{selectedCanvasId:void 0}},f.parameters={...f.parameters,docs:{...f.parameters?.docs,source:{originalSource:`{
  play: async ({
    args,
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('navigation', {
      name: 'Canvas switcher'
    })).toBeVisible();
    await userEvent.click(canvas.getByText('Canvases'));
    const input = canvas.getByRole('textbox', {
      name: 'Canvas name'
    });
    await userEvent.clear(input);
    await userEvent.type(input, 'Renamed canvas');
    await userEvent.click(canvas.getByRole('button', {
      name: 'Rename'
    }));
    await expect(args.onRename).toHaveBeenCalledWith('canvas-1', 'Renamed canvas');
  }
}`,...f.parameters?.docs?.source}}},p.parameters={...p.parameters,docs:{...p.parameters?.docs,source:{originalSource:`{
  args: {
    selectedCanvasId: undefined
  }
}`,...p.parameters?.docs?.source}}},m=[`Selected`,`NewCanvas`]})))()}h();export{p as NewCanvas,f as Selected,m as __namedExportsOrder,d as default};