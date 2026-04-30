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
  }
  export default class Plot extends Component<PlotParams> {}
}
