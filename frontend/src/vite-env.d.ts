/// <reference types="vite/client" />

declare module "react-plotly.js" {
  import { Component } from "react";
  interface PlotParams {
    data: any[];
    layout?: any;
    config?: any;
    style?: any;
    useResizeHandler?: boolean;
    onClick?: (e: any) => void;
    onHover?: (e: any) => void;
    onRelayout?: (e: any) => void;
    onInitialized?: (figure: any, graphDiv: any) => void;
  }
  export default class Plot extends Component<PlotParams> {}
}

// Same dist bundle react-plotly.js imports, so calls land on the same module
// instance as the rendered chart. Only the imperative methods used here.
declare module "plotly.js/dist/plotly" {
  interface PlotlyStatic {
    restyle(root: any, aobj: any, traces?: number[]): void;
  }
  const Plotly: PlotlyStatic;
  export default Plotly;
}
