import TypeAnimation from "react-type-animation";
import styled, { keyframes } from "styled-components";
import { fadeInLeft, fadeInRight, fadeInUp } from "react-animations";
import Stack from "@mui/material/Stack";
import Contact from "./Contact";
import Navigation from "./Navigation";

const fadeInLeftAnimation = keyframes`${fadeInLeft}`;
const fadeInRightAnimation = keyframes`${fadeInRight}`;
const fadeInUpAnimation = keyframes`${fadeInUp}`;
const FadeInRightDiv = styled.div`
  animation: 1s ${fadeInRightAnimation};
`;
const FadeInLeftDiv = styled.div`
  animation: 1s ${fadeInLeftAnimation};
`;
const FadeInUpDiv = styled.div`
  animation: 1s ${fadeInUpAnimation};
`;

const Home = ({ onClick }) => {
  return (
    <Stack
      direction="column"
      justifyContent="center"
      spacing={1}
      fontSize="x-large"
      height="100%"
    >
      <TypeAnimation
        cursor={true}
        sequence={["Hi, I'm Aaron Ang"]}
        wrapper="h1"
      />
      <FadeInRightDiv>
        <h2>An aspiring software developer</h2>
      </FadeInRightDiv>
      <FadeInLeftDiv>
        <Contact />
      </FadeInLeftDiv>
      <FadeInUpDiv>
        <Navigation onClick={onClick} />
      </FadeInUpDiv>
    </Stack>
  );
};

export default Home;
