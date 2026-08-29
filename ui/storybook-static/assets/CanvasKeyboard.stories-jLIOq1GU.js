import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{n as t}from"./iframe-CXgjsIO4.js";import{t as n}from"./jsx-runtime-DeHZSEgm.js";import{a as r,i}from"./PaperNode-BEx7mgbs.js";import{n as a,t as o}from"./Canvas-Cs133t-o.js";import{n as s,t as c}from"./researchData-CBM3CDD8.js";function l(){let[e,t]=(0,u.useState)();return(0,d.jsx)(i,{papers:[s[0]],relationships:[],children:(0,d.jsx)(o,{selectedPaperId:e,onPaperSelect:t,onRelayout:()=>void 0})})}var u,d,f,p,m,h,g,_,v;function y(){return(y=e((()=>{u=t(),c(),a(),r(),d=n(),{expect:f,userEvent:p,waitFor:m,within:h}=__STORYBOOK_MODULE_TEST__,g={title:`Research/CanvasKeyboard`,component:l},_={play:async({canvasElement:e})=>{let t=h(e);(await m(()=>t.getByRole(`article`,{name:s[0].title}))).focus(),await p.keyboard(`{Enter}`),await f(t.getByRole(`complementary`,{name:s[0].title})).toBeVisible(),await f(t.getByRole(`button`,{name:`Relayout`})).toBeVisible()}},_.parameters={..._.parameters,docs:{..._.parameters?.docs,source:{originalSource:`{
  play: async ({
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    const node = await waitFor(() => canvas.getByRole('article', {
      name: papers[0].title
    }));
    node.focus();
    await userEvent.keyboard('{Enter}');
    await expect(canvas.getByRole('complementary', {
      name: papers[0].title
    })).toBeVisible();
    await expect(canvas.getByRole('button', {
      name: 'Relayout'
    })).toBeVisible();
  }
}`,..._.parameters?.docs?.source}}},v=[`SelectAndOpenPaperControls`]})))()}y();export{_ as SelectAndOpenPaperControls,v as __namedExportsOrder,g as default};