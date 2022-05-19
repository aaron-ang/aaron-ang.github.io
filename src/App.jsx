import "./App.css";
import React, { Suspense } from "react";
import Home from "./pages/Home/Home";
import { useRef } from "react";
// import {Routes, Route, Link } from "react-router-dom";
import { Parallax, ParallaxLayer } from "@react-spring/parallax";

function App() {
  const ref = useRef();

  const About = React.lazy(() => import("./pages/About"));
  const Projects = React.lazy(() => import("./pages/Projects"));
  const Skills = React.lazy(() => import("./pages/Skills"));

  const handleClick = (e) => {
    const targetId = e.currentTarget.id;
    switch (targetId) {
      case "about":
        ref.current.scrollTo(1);
        break;
      case "projects":
        ref.current.scrollTo(2);
        break;
      case "skills":
        ref.current.scrollTo(3);
        break;
      default:
        break;
    }
  };

  return (
    <Parallax pages={4} ref={ref}>
      <ParallaxLayer>
        <Home onClick={handleClick} />
      </ParallaxLayer>
      <Suspense>
        <ParallaxLayer offset={1}>
          <About />
        </ParallaxLayer>
        <ParallaxLayer offset={2}>
          <Projects />
        </ParallaxLayer>
        <ParallaxLayer offset={3}>
          <Skills />
        </ParallaxLayer>
      </Suspense>
    </Parallax>
  );
}

export default App;
