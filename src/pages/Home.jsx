import '../App.css'
import TypeAnimation from "react-type-animation";
import { FiGithub, FiLinkedin, FiMail } from "react-icons/fi";
import IconButton from "@mui/material/IconButton";
import styled, { keyframes } from "styled-components";
import { fadeInLeft, fadeInRight } from "react-animations";

const fadeInLeftAnimation = keyframes`${fadeInLeft}`;
const fadeInRightAnimation = keyframes`${fadeInRight}`;
const FadeInRightDiv = styled.div`animation: 1s ${fadeInRightAnimation}`
const FadeInLeftDiv = styled.div`animation: 1s ${fadeInLeftAnimation}`

export default function Home() {
  return (
    <>
      <TypeAnimation cursor={true} sequence={["Hi, I'm Aaron"]} wrapper="h1" />
      <FadeInRightDiv>
        <h2>Aspiring software developer</h2>
      </FadeInRightDiv>
      <FadeInLeftDiv>
        <IconButton
          aria-label="github"
          onClick={() => window.open("https://github.com/aaron-ang")}
        >
          <FiGithub />
        </IconButton>
        <IconButton
          aria-label="linkedin"
          onClick={() => window.open("https://www.linkedin.com/in/aaron-ayd/")}
        >
          <FiLinkedin />
        </IconButton>
        <IconButton
          aria-label="email"
          onClick={() => window.open("mailto:aaron.angyd@gmail.com")}
        >
          <FiMail />
        </IconButton>
      </FadeInLeftDiv>
    </>
  );
}
