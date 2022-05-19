import { useRef } from "react";
import useOnScreen from "./isIntersecting";
import Stack from "@mui/material/Stack";
import CardMedia from "@mui/material/CardMedia";
import { scale } from "react-animations/lib/utils";
import styled, { keyframes } from "styled-components";
import { fadeIn } from "react-animations";

const skills = [
  {
    title: "Front-End",
    names: ["HTML", "CSS", "JavaScript", "React"],
  },
  {
    title: "Back-End",
    names: ["SQL", "Node.js", "Flask"],
  },
  {
    title: "Other",
    names: ["Python", "Java", "Kotlin", "C++", "Git", "Matplotlib", "Numpy"],
  },
];

const fadeInAnimation = keyframes`${fadeIn}`;
const FadeInDiv = styled.div`
  animation: 1s ${fadeInAnimation} ease-in;
`;

const Skill = ({ name }) => {
  return (
    <Stack direction="column">
      <CardMedia
        component="img"
        image={`/skills/${name}.png`}
        alt="skillIcon"
        height="75em"
        sx={{
          transition: "all .1s linear",
          ":hover": { transform: scale(1.1) },
        }}
      />
      <p>{name}</p>
    </Stack>
  );
};

const Skills = () => {
  const ref = useRef();
  const isVisible = useOnScreen(ref);

  return (
    <Stack
      direction={{ sm: "row", md: "column" }}
      justifyContent="center"
      height="100%"
      ref={ref}
    >
      {isVisible && (
        <FadeInDiv>
          {skills.map((cat) => (
            <Stack
              direction={{ sm: "column", md: "row" }}
              justifyContent="center"
              alignItems="center"
              spacing={3}
              margin={3}
            >
              <h2>{cat.title}</h2>
              {cat.names.map((s) => (
                <Skill name={s} />
              ))}
            </Stack>
          ))}
        </FadeInDiv>
      )}
    </Stack>
  );
};

export default Skills;
