import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes, NavLink, Navigate } from "react-router-dom";
import App from "./App";
import { PortfolioProvider } from "./portfolio";
import "./styles.css";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000 } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <PortfolioProvider>
          <App />
        </PortfolioProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

export { NavLink, Routes, Route, Navigate };
