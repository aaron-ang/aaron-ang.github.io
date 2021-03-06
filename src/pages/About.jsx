import { useInView } from "react-intersection-observer";

const About = () => {
  const { ref, inView, entry } = useInView({ triggerOnce: false });

  return (
    <>
      <h1>About</h1>
      <div>I was born and raised in Singapore πΈπ¬</div>
      <div>
        I am currently pursuing a double major in Computer Science and Economics
        at Boston University.
      </div>
      <div>
        You will find me playing basketball π, reading non-fiction π, or
        with my <a href="https://opendrinks.io/recipe/teh-c">goto-drink</a> π
      </div>
      <div>I am interested in all things tech!</div>
    </>
  );
};

export default About;
