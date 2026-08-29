import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{n as t}from"./iframe-CXgjsIO4.js";import{t as n}from"./jsx-runtime-DeHZSEgm.js";import{a as r,i}from"./PaperNode-BEx7mgbs.js";import{n as a,t as o}from"./Canvas-Cs133t-o.js";import{n as s,r as c,t as l}from"./researchData-CBM3CDD8.js";function u(){let[e,t]=(0,d.useState)();return(0,f.jsx)(i,{papers:s,relationships:c,editRelationship:v,children:(0,f.jsx)(o,{selectedRelationshipId:e,onRelationshipSelect:t})})}var d,f,p,m,h,g,_,v,y,b,x;function S(){return(S=e((()=>{d=t(),l(),a(),r(),f=n(),{expect:p,fn:m,userEvent:h,waitFor:g,within:_}=__STORYBOOK_MODULE_TEST__,v=m(),y={title:`Research/RelationshipEdge`,component:u},b={play:async({canvasElement:e})=>{let t=_(e);(await g(()=>t.getByRole(`button`,{name:`Relationship: ${c[0].label}`}))).focus(),await h.keyboard(`{Enter}`);let n=await g(()=>t.getByRole(`form`,{name:`Edit relationship: ${c[0].label}`})),r=_(n).getByRole(`textbox`,{name:`Label`});await h.clear(r),await h.type(r,`Keyboard-edited relationship{Enter}`),await p(v).toHaveBeenCalledWith(c[0].id,p.objectContaining({label:`Keyboard-edited relationship`})),await p(t.getByRole(`complementary`,{name:`Relationship detail: ${c[0].label}`})).toBeVisible()}},b.parameters={...b.parameters,docs:{...b.parameters?.docs,source:{originalSource:`{
  play: async ({
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    const edge = await waitFor(() => canvas.getByRole('button', {
      name: \`Relationship: \${relationships[0].label}\`
    }));
    edge.focus();
    await userEvent.keyboard('{Enter}');
    const form = await waitFor(() => canvas.getByRole('form', {
      name: \`Edit relationship: \${relationships[0].label}\`
    }));
    const label = within(form).getByRole('textbox', {
      name: 'Label'
    });
    await userEvent.clear(label);
    await userEvent.type(label, 'Keyboard-edited relationship{Enter}');
    await expect(editRelationship).toHaveBeenCalledWith(relationships[0].id, expect.objectContaining({
      label: 'Keyboard-edited relationship'
    }));
    await expect(canvas.getByRole('complementary', {
      name: \`Relationship detail: \${relationships[0].label}\`
    })).toBeVisible();
  }
}`,...b.parameters?.docs?.source}}},x=[`KeyboardEdit`]})))()}S();export{b as KeyboardEdit,x as __namedExportsOrder,y as default};