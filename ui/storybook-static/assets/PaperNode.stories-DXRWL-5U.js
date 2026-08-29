import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{t}from"./jsx-runtime-DeHZSEgm.js";import{a as n,b as r,d as i,i as a,n as o,r as s,t as c,x as l}from"./PaperNode-BEx7mgbs.js";import{n as u,t as d}from"./researchData-CBM3CDD8.js";import{t as f}from"./utils-BPwTFDae.js";var p,m,h,g,_,v,y,b,x,S,C,w,T,E,D,O,k,A,j;function M(){return(M=e((()=>{l(),d(),s(),n(),f(),p=t(),{expect:m,fn:h,userEvent:g,waitFor:_,within:v}=__STORYBOOK_MODULE_TEST__,y={paper:o},b=h(),x=h(),S=h(),C=h(),w=h(),T={id:u[0].id,data:{paper:u[0]},type:`paper`,dragging:!1,zIndex:0,selectable:!0,deletable:!0,selected:!1,draggable:!0,isConnectable:!0,positionAbsoluteX:0,positionAbsoluteY:0},E={title:`Research/PaperNode`,component:o,args:T,render:({data:e,selected:t})=>{let n={...i(e.paper),selected:t};return(0,p.jsx)(a,{editPaper:b,retryPaperProcessing:x,removePaper:S,editReview:C,regeneratePaperReview:w,children:(0,p.jsxs)(`div`,{style:{width:`100vw`,height:`100vh`},children:[(0,p.jsx)(r,{defaultNodes:[n],nodeTypes:y,fitView:!0},`${e.paper.id}-${t}`),t?(0,p.jsx)(c,{paper:e.paper}):null]})})}},D={},O={args:{data:{paper:{...u[0],year:null,month:null}}},play:async({canvasElement:e})=>{await _(()=>m(v(e).getByText(`Date unknown`)).toBeVisible())}},k={args:{selected:!0},play:async({canvasElement:e})=>{let t=v(e);await m(t.getByRole(`form`,{name:`Edit metadata for ${u[0].title}`})).toBeVisible();let n=t.getByRole(`textbox`,{name:`Title`});await g.clear(n),await g.type(n,`Edited paper title`),await g.click(t.getByRole(`button`,{name:`Save paper`})),await m(b).toHaveBeenCalledWith(u[0].id,m.objectContaining({title:`Edited paper title`}))}},A={args:{selected:!0,data:{paper:{...u[0],review:void 0,processing_status:`failed`,error:`No usable paper text was available.`}}},play:async({canvasElement:e})=>{let t=v(e);await g.click(t.getByRole(`button`,{name:`Retry`})),await m(x).toHaveBeenCalledWith(u[0].id),await g.click(t.getByRole(`button`,{name:`Delete paper`})),await m(S).toHaveBeenCalledWith(u[0].id)}},D.parameters={...D.parameters,docs:{...D.parameters?.docs,source:{originalSource:`{}`,...D.parameters?.docs?.source}}},O.parameters={...O.parameters,docs:{...O.parameters?.docs,source:{originalSource:`{
  args: {
    data: {
      paper: {
        ...papers[0],
        year: null,
        month: null
      }
    }
  },
  play: async ({
    canvasElement
  }) => {
    await waitFor(() => expect(within(canvasElement).getByText('Date unknown')).toBeVisible());
  }
}`,...O.parameters?.docs?.source}}},k.parameters={...k.parameters,docs:{...k.parameters?.docs,source:{originalSource:`{
  args: {
    selected: true
  },
  play: async ({
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('form', {
      name: \`Edit metadata for \${papers[0].title}\`
    })).toBeVisible();
    const title = canvas.getByRole('textbox', {
      name: 'Title'
    });
    await userEvent.clear(title);
    await userEvent.type(title, 'Edited paper title');
    await userEvent.click(canvas.getByRole('button', {
      name: 'Save paper'
    }));
    await expect(editPaper).toHaveBeenCalledWith(papers[0].id, expect.objectContaining({
      title: 'Edited paper title'
    }));
  }
}`,...k.parameters?.docs?.source}}},A.parameters={...A.parameters,docs:{...A.parameters?.docs,source:{originalSource:`{
  args: {
    selected: true,
    data: {
      paper: {
        ...papers[0],
        review: undefined,
        processing_status: 'failed',
        error: 'No usable paper text was available.'
      }
    }
  },
  play: async ({
    canvasElement
  }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', {
      name: 'Retry'
    }));
    await expect(retryPaperProcessing).toHaveBeenCalledWith(papers[0].id);
    await userEvent.click(canvas.getByRole('button', {
      name: 'Delete paper'
    }));
    await expect(removePaper).toHaveBeenCalledWith(papers[0].id);
  }
}`,...A.parameters?.docs?.source}}},j=[`Default`,`UnknownDate`,`Editable`,`Failed`]})))()}M();export{D as Default,k as Editable,A as Failed,O as UnknownDate,j as __namedExportsOrder,E as default};