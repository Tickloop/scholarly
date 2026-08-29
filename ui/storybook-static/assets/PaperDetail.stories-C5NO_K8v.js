import{n as e}from"./rolldown-runtime-DkW27tQK.js";import{n as t,t as n}from"./PaperDetail-Dm58zfbf.js";import{n as r,t as i}from"./researchData-CBM3CDD8.js";var a,o,s,c,l,u;function d(){return(d=e((()=>{i(),t(),{expect:a,within:o}=__STORYBOOK_MODULE_TEST__,s={title:`Research/PaperDetail`,component:n,args:{paper:r[0],review:r[0].review},argTypes:{paper:{control:`object`},review:{control:`object`}}},c={},l={args:{paper:{...r[0],year:null,month:null}},play:async({canvasElement:e})=>{await a(o(e).getByText(`Date unknown`)).toBeVisible()}},c.parameters={...c.parameters,docs:{...c.parameters?.docs,source:{originalSource:`{}`,...c.parameters?.docs?.source}}},l.parameters={...l.parameters,docs:{...l.parameters?.docs,source:{originalSource:`{
  args: {
    paper: {
      ...papers[0],
      year: null,
      month: null
    }
  },
  play: async ({
    canvasElement
  }) => {
    await expect(within(canvasElement).getByText('Date unknown')).toBeVisible();
  }
}`,...l.parameters?.docs?.source}}},u=[`Default`,`UnknownDate`]})))()}d();export{c as Default,l as UnknownDate,u as __namedExportsOrder,s as default};